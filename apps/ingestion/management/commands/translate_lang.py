import json
import math

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.catalog.models import ProductText
from apps.corecfg.models import ConfigEntry, Language, get_config
from apps.ingestion.models import Offer, OfferText

OPENAI_API_URL = "https://api.openai.com/v1"
BATCH_ENDPOINT = f"{OPENAI_API_URL}/batches"
FILES_ENDPOINT = f"{OPENAI_API_URL}/files"
DEFAULT_MODEL = "gpt-4.1"
CONFIG_BATCH_PREFIX = "translation_batch_id_"
CONFIG_LAST_RUN_PREFIX = "translation_last_run_"

LANG_NAMES = {
    "lt": "lietuvių",
    "lv": "latvių",
    "et": "estų",
    "pl": "lenkų",
    "de": "vokiečių",
}

# Žvejybos domeno kontekstas – padeda modeliui suprasti terminiją.
# {lang} pakeičiama tikslinės kalbos pavadinimu (gen.).
SYSTEM_PROMPT_TEMPLATE = """Tu esi profesionalus vertėjas iš anglų į {lang} kalbą, specializuojantis žvejybos reikmenų tekstuose.

Kontekstas: prekės yra žvejybos įranga (meškerės, ritės, valai, kabliai, masalai, vobleriai, aksesuarai) iš didmenininkų FOX International, CC Moore, MOSS/Delphin.

Terminų žodynėlis (naudok teisingas {lang} atitikmenis):
- rod = meškerė; fishing rod = meškerė
- reel = ritė; spinning reel = ritė su fiksuota ritės lėkšte
- line = valas; braided line = pinta vala; monofilament line = monofilamentinis valas
- hook = kablys; hooklink = kabliuko pavadėlis
- bait = masalas/jaukas (kontekste); boilie = boilius; wafter = plūduriuojantis boilius
- lure = masalas (vobleris, blizgė); spinner = blizgė; wobbler = vobleris
- rig = sistema/pavadėlis; leader = pavadėlis; terminal tackle = pavadėlių furnitūra
- test curve = testo kreivė; casting weight = svoris metimui (g)
- drag = stabdis; drag force = stabdymo jėga
- groundbait = jaukas (sausa jauko mišinys)
- bankstick = meškerės atrama; rod pod = stovas meškerėms
- bite alarm = signalizatorius; bobbin/hanger = kablys signalizatoriui
- net = graibštas; landing net = išėmimo graibštas; keepnet = krepšis žuviai
- tackle box = dėžutė; luggage = krepšiai
- shelter/bivvy = pavėsinė; bedchair = gultas; chair = kėdė
- spod = masalo raketa; marker float = žymeklio plūdė
- pole = bologninė meškerė; feeder = feederio meškerė; match = varžybinė meškerė
- catapult = svaidyklė; throw stick = metymo lazda
- free spool = laisva ritė; baitrunner = laisvos ritės režimas
- EAN/barcode, kainos, matavimo vienetai (g, kg, m, mm, cm, lbs, ft, yds) – NEVERČIAMI
- Prekių modeliai, kodai (pvz., GRD221, CAC908), prekių ženklai – NEVERČIAMI

Vardo taisyklė:
- Jei `name` yra tik modelio kodas arba prekės ženklas + modelis (pvz., "Delphin TOKYO"), PRIDĖK prekės esmę/kategoriją: "Delphin TOKYO monofilamentinis valas", ne tik "Delphin TOKYO".
- Esmę išvesk iš `description`/`technical_description`/`category` konteksto (valas, ritė, meškerė, graibštas, boilius...). Jei neaišku – pridėk "prekė" arba bendrą žodį, bet nepersistenk.
- SEO `seo_title` taip pat turi turėti prekės esmę.

Formatavimas:
- Išvesk JSON objektą su laukais: "name", "base_name", "description", "technical_description", "seo_title", "seo_description", "seo_keywords" (jei kuris nors tuščias įvesčiai – gali generuoti iš konteksto).
- seo_title: iki 60 simbolių, su prekės esme + prekės ženklu.
- seo_description: 150–160 simbolių, pardaviminis.
- seo_keywords: 5–10 raktažodžių kableliais (be prekės kodo).
- Jei tekstas HTML – išlaikyk žymes nepakeistas.
- Teksto tonas: informatyvus, pardaviminis (e-komercija).
- Jei sąrašas – išlaikyk struktūrą; nepersistenk su skyryba."""

# SEO-only režimas: turimus (bet kurios kalbos) tekstus paimame kaip kontekstą
# ir generuojame tik seo_* laukus, neperrašydami name/description.
SEO_SYSTEM_PROMPT_TEMPLATE = """Tu esi SEO specialistas, rašantis {lang} kalba žvejybos reikmenų e-komercijai.

Gausi prekės duomenis (pavadinimas, aprašymas, kategorija, prekės ženklas – gali būti bet kuria kalba).
Sugeneruok tik SEO laukus {lang} kalba.

Taisyklės:
- seo_title: iki 60 simbolių, privalo turėti prekės esmę (meškerė, ritė, valas, minkštas masalas, boilius...) + prekės ženklą. Jei pavadinime tik modelio kodas – esmę išvesk iš kategorijos/aprašymo.
- seo_description: 150–160 simbolių, natūralus pardaviminis tekstas.
- seo_keywords: 5–10 raktažodžių kableliais, {lang} kalba (be prekės kodo/EAN).
- Prekių ženklai, modelių kodai, vienetai – neverčiami.
- Išvesk JSON objektą TIK su laukais: "seo_title", "seo_description", "seo_keywords"."""


def _estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / 4)


def _openai_headers() -> dict:
    key = getattr(settings, "OPENAI_API_KEY", "") or ""
    if not key:
        raise CommandError("OPENAI_API_KEY nenustatytas (.env).")
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def _config_key(prefix: str, lang_code: str) -> str:
    return f"{prefix}{lang_code}"


class Command(BaseCommand):
    help = (
        "Verčia offer'ių tekstus į tikslinę kalbą per OpenAI Batch API (lėtas, pigus).\n"
        "Eiga: --prepare (sugeneruoja JSONL) -> --submit (įkelia batch'ą) ->\n"
        "--status (patikrina) -> --apply (įrašo atgal į OfferText/ProductText).\n"
        "--language lt (default); ateityje lv/et/pl/de ir kt."
    )

    def add_arguments(self, parser):
        parser.add_argument("--language", default="lt", help="Tikslinė kalba (lt/lv/et/pl/de...)")
        parser.add_argument("--prepare", action="store_true", help="Sugeneruoti JSONL failą")
        parser.add_argument("--submit", action="store_true", help="Pateikti batch'ą OpenAI")
        parser.add_argument("--status", action="store_true", help="Patikrinti batch'o būseną")
        parser.add_argument("--apply", action="store_true", help="Įrašyti vertimus į DB")
        parser.add_argument("--batch-id", help="Ranka nurodytas batch_id")
        parser.add_argument("--model", default=None, help="OpenAI modelis (default iš config)")
        parser.add_argument("--limit", type=int, default=0, help="Riboti eilučių skaičių")
        parser.add_argument("--force", action="store_true", help="Versti viską iš naujo")
        parser.add_argument(
            "--seo-only",
            action="store_true",
            help="Generuoti tik seo_* laukus (name/description neperrašomi; EN rašoma į Product.seo_*)",
        )
        parser.add_argument("--out", default=None, help="Išvesties failas")

    def _language(self, code: str) -> Language:
        lang = Language.objects.filter(code=code, is_active=True).first()
        if not lang:
            raise CommandError(f"Language '{code}' nerastas arba neaktyvus (corecfg).")
        return lang

    def _system_prompt(self, lang: Language) -> str:
        lang_name = LANG_NAMES.get(lang.code, lang.name or lang.code)
        return SYSTEM_PROMPT_TEMPLATE.format(lang=lang_name)

    def _seo_prompt(self, lang: Language) -> str:
        lang_name = LANG_NAMES.get(lang.code, lang.name or lang.code)
        return SEO_SYSTEM_PROMPT_TEMPLATE.format(lang=lang_name)

    def handle(self, *args, **options):
        lang = self._language(options["language"])
        options.setdefault("out", options["out"] or f"translation_{lang.code}_batch.jsonl")

        if options["seo_only"]:
            options["out"] = (
                options["out"]
                if options["out"] != f"translation_{lang.code}_batch.jsonl"
                else f"seo_{lang.code}_batch.jsonl"
            )

        if options["prepare"]:
            self._prepare(lang, options)
        elif options["submit"]:
            self._submit(lang, options)
        elif options["status"]:
            self._status(lang, options)
        elif options["apply"]:
            self._apply(lang, options)
        else:
            raise CommandError("Nurodyk veiksmą: --prepare/--submit/--status/--apply")

    # ---------- prepare ----------

    def _offers_to_translate(self, lang, force: bool):
        qs = Offer.objects.filter(is_published=True).select_related("product")
        if not force:
            qs = qs.exclude(
                id__in=OfferText.objects.filter(language=lang).values_list("offer_id", flat=True)
            )
        return qs

    def _seo_targets(self, lang, force: bool):
        """`custom_id` prefiksas nusako taikinį: 'ot:<id>' OfferText, 'p:<id>' Product.

        - bazinė kalba (en): SEO rašoma į Product.seo_* (nėra OfferText en)
        - kitos kalbos: į OfferText(target lang).seo_*, propaguojama į ProductText
        """
        targets: list[tuple[str, object, dict]] = []
        if lang.is_base:
            from apps.catalog.models import Product

            products = Product.objects.filter(
                is_active=True, offers__is_published=True
            ).distinct()
            if not force:
                products = products.filter(seo_title="")
            for product in products.iterator():
                context = {
                    "name": product.name or "",
                    "description": product.description or "",
                    "category": product.category_raw or product.sub_category_raw or "",
                    "brand": product.brand.name if product.brand_id else "",
                }
                targets.append((f"p:{product.id}", product, context))
            return targets

        texts = OfferText.objects.filter(
            language=lang, offer__is_published=True
        ).select_related("offer", "offer__product")
        if not force:
            texts = texts.filter(seo_title="")
        for text in texts.iterator():
            offer = text.offer
            context = {
                "name": text.name or offer.name or "",
                "description": text.description or offer.description or "",
                "category": offer.category_raw or offer.sub_category_raw or "",
                "brand": offer.brand_name_raw or "",
            }
            targets.append((f"ot:{text.id}", text, context))
        return targets

    def _prepare(self, lang, options):
        seo_only = options["seo_only"]
        model = options["model"] or get_config("translation_model", DEFAULT_MODEL)

        if seo_only:
            targets = self._seo_targets(lang, options["force"])
        else:
            targets = []
            for offer in self._offers_to_translate(lang, options["force"]):
                fields = {
                    "name": offer.name or "",
                    "base_name": offer.base_name or "",
                    "description": offer.description or "",
                    "technical_description": offer.technical_description or "",
                    "category": offer.category_raw or offer.sub_category_raw or "",
                    "brand": offer.brand_name_raw or "",
                }
                targets.append((f"o:{offer.id}", offer, fields))

        if options["limit"]:
            targets = targets[: options["limit"]]
        if not targets:
            self.stdout.write(f"Nieko daryti '{lang.code}' – viskas jau užpildyta.")
            return

        total_tokens = 0
        system_prompt = self._seo_prompt(lang) if seo_only else self._system_prompt(lang)
        out_tokens = 300 if seo_only else 800
        written = 0
        with open(options["out"], "w", encoding="utf-8") as handle:
            for custom_id, _obj, fields in targets:
                if not any(fields.values()):
                    continue
                body = {
                    "model": model,
                    "temperature": 0.2,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": json.dumps(fields, ensure_ascii=False)},
                    ],
                }
                line = {
                    "custom_id": custom_id,
                    "method": "POST",
                    "url": "/v1/chat/completions",
                    "body": body,
                }
                handle.write(json.dumps(line, ensure_ascii=False) + "\n")
                written += 1
                total_tokens += (
                    _estimate_tokens(system_prompt)
                    + _estimate_tokens(json.dumps(fields))
                    + out_tokens
                )

        self.stdout.write(self.style.SUCCESS(
            f"Paruošta {written} eilučių -> {options['out']}\n"
            f"Kalba: {lang.code} | Modelis: {model} | Režimas: {'seo-only' if seo_only else 'full'}\n"
            f"Apytikslis token'ų įvertis: ~{total_tokens:,}\n"
            f"(Batch API = 50% nuolaida)"
        ))

    # ---------- submit ----------

    def _submit(self, lang, options):
        file_path = options["out"]
        try:
            with open(file_path, "rb") as handle:
                upload = requests.post(
                    FILES_ENDPOINT,
                    headers={"Authorization": f"Bearer {settings.OPENAI_API_KEY}"},
                    files={"file": (file_path, handle, "application/jsonl")},
                    data={"purpose": "batch"},
                    timeout=300,
                )
        except FileNotFoundError as exc:
            raise CommandError(f"Failas {file_path} nerastas – paleisk --prepare pirmiausia.") from exc
        upload.raise_for_status()
        file_id = upload.json()["id"]
        self.stdout.write(f"Uploaded file: {file_id}")

        model = options["model"] or get_config("translation_model", DEFAULT_MODEL)
        batch = requests.post(
            BATCH_ENDPOINT,
            headers=_openai_headers(),
            json={
                "input_file_id": file_id,
                "endpoint": "/v1/chat/completions",
                "completion_window": "24h",
                "metadata": {"kind": f"translation_{lang.code}", "model": model},
            },
            timeout=60,
        )
        batch.raise_for_status()
        batch_id = batch.json()["id"]
        ConfigEntry.objects.update_or_create(
            key=_config_key(CONFIG_BATCH_PREFIX, lang.code), defaults={"value": batch_id}
        )
        ConfigEntry.objects.update_or_create(
            key=_config_key(CONFIG_LAST_RUN_PREFIX, lang.code),
            defaults={"value": timezone.now().isoformat()},
        )
        self.stdout.write(self.style.SUCCESS(f"Batch pateiktas: {batch_id}"))

    # ---------- status ----------

    def _status(self, lang, options):
        batch_id = options.get("batch_id") or get_config(_config_key(CONFIG_BATCH_PREFIX, lang.code), "")
        if not batch_id:
            raise CommandError("Batch ID nerastas (pateik --batch-id arba --submit).")
        response = requests.get(
            f"{BATCH_ENDPOINT}/{batch_id}", headers=_openai_headers(), timeout=30
        )
        response.raise_for_status()
        data = response.json()
        self.stdout.write(
            f"Batch {batch_id} ({lang.code}): status={data.get('status')} "
            f"completed={data.get('request_counts', {}).get('completed', 0)}/"
            f"{data.get('request_counts', {}).get('total', 0)} "
            f"failed={data.get('request_counts', {}).get('failed', 0)}"
        )
        if data.get("status") == "completed":
            self.stdout.write(
                f"Output file: {data.get('output_file_id')} (galima --apply)"
            )

    # ---------- apply ----------

    def _apply(self, lang, options):
        batch_id = options.get("batch_id") or get_config(_config_key(CONFIG_BATCH_PREFIX, lang.code), "")
        if not batch_id:
            raise CommandError("Batch ID nerastas.")
        response = requests.get(
            f"{BATCH_ENDPOINT}/{batch_id}", headers=_openai_headers(), timeout=30
        )
        response.raise_for_status()
        data = response.json()
        if data.get("status") != "completed":
            raise CommandError(f"Batch dar nesibaigęs: {data.get('status')}")
        output_file = data.get("output_file_id")
        if not output_file:
            raise CommandError("Batch neturi output_file_id.")

        out = requests.get(
            f"{FILES_ENDPOINT}/{output_file}/content",
            headers=_openai_headers(),
            timeout=300,
        )
        out.raise_for_status()

        rows = [json.loads(l) for l in out.text.splitlines() if l.strip()]
        offer_ids, text_ids, product_ids = [], [], []
        for row in rows:
            prefix, _, raw_id = str(row.get("custom_id", "")).partition(":")
            if not raw_id.isdigit():
                continue
            if prefix == "ot":
                text_ids.append(int(raw_id))
            elif prefix == "p":
                product_ids.append(int(raw_id))
            else:
                offer_ids.append(int(raw_id))

        from apps.catalog.models import Product

        offers = {o.id: o for o in Offer.objects.filter(id__in=offer_ids).select_related("product")}
        texts = {
            t.id: t
            for t in OfferText.objects.filter(id__in=text_ids).select_related("offer", "offer__product")
        }
        products = {p.id: p for p in Product.objects.filter(id__in=product_ids)}

        ok = errors = 0
        for row in rows:
            prefix, _, raw_id = str(row.get("custom_id", "")).partition(":")
            content = self._row_content(row)
            if content is None or not raw_id.isdigit():
                errors += 1
                continue
            seo = self._seo_fields(content)

            if prefix == "p":
                product = products.get(int(raw_id))
                if not product:
                    errors += 1
                    continue
                for key, value in seo.items():
                    if value:
                        setattr(product, key, value)
                product.save(update_fields=list(seo.keys()) + ["updated_at"])
                ok += 1
                continue

            if prefix == "ot":
                text = texts.get(int(raw_id))
                if not text:
                    errors += 1
                    continue
                for key, value in seo.items():
                    if value:
                        setattr(text, key, value)
                text.save(update_fields=list(seo.keys()) + ["updated_at"])
                offer = text.offer
                if offer and offer.product_id:
                    self._propagate_seo_to_product(offer, lang, seo)
                ok += 1
                continue

            offer = offers.get(int(raw_id))
            if not offer:
                errors += 1
                continue
            fields = {
                "name": (content.get("name") or "").strip(),
                "base_name": (content.get("base_name") or "").strip(),
                "description": (content.get("description") or "").strip(),
                "technical_description": (content.get("technical_description") or "").strip(),
                **seo,
            }
            OfferText.objects.update_or_create(offer=offer, language=lang, defaults=fields)
            if offer.product_id:
                self._propagate_to_product(offer, lang, fields)
            ok += 1

        self.stdout.write(self.style.SUCCESS(
            f"Įrašyta {ok} '{lang.code}' įrašų (klaidų: {errors})."
        ))

    @staticmethod
    def _row_content(row) -> dict | None:
        body = (row.get("response") or {}).get("body") or {}
        choices = body.get("choices") or []
        if not choices:
            return None
        try:
            return json.loads(choices[0]["message"]["content"])
        except (json.JSONDecodeError, KeyError, IndexError):
            return None

    @staticmethod
    def _seo_fields(content: dict) -> dict:
        return {
            "seo_title": (content.get("seo_title") or "").strip()[:255],
            "seo_description": (content.get("seo_description") or "").strip()[:320],
            "seo_keywords": (content.get("seo_keywords") or "").strip()[:255],
        }

    def _propagate_seo_to_product(self, offer, lang, seo):
        text = ProductText.objects.filter(
            product_id=offer.product_id, language=lang
        ).first()
        if text is None:
            ProductText.objects.create(
                product_id=offer.product_id,
                language=lang,
                source_offer=offer,
                **seo,
            )
            return
        if text.source_offer_id not in (None, offer.id):
            return
        changed = [k for k, v in seo.items() if v and getattr(text, k) != v]
        for key in changed:
            setattr(text, key, seo[key])
        if changed:
            text.source_offer = offer
            text.save(update_fields=[*changed, "source_offer", "updated_at"])

    def _propagate_to_product(self, offer, lang, fields):
        from django.utils.text import slugify

        text = ProductText.objects.filter(
            product_id=offer.product_id, language=lang
        ).first()
        if text is None:
            ProductText.objects.create(
                product_id=offer.product_id,
                language=lang,
                source_offer=offer,
                slug=slugify(fields["name"])[:220] if fields.get("name") else "",
                **fields,
            )
            return
        if text.source_offer_id not in (None, offer.id):
            return
        changed = []
        for key, value in fields.items():
            if value and getattr(text, key) != value:
                setattr(text, key, value)
                changed.append(key)
        if not text.slug and fields.get("name"):
            text.slug = slugify(fields["name"])[:220]
            changed.append("slug")
        if changed:
            text.source_offer = offer
            text.save(update_fields=[*changed, "source_offer", "updated_at"])
