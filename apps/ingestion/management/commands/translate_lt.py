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
DEFAULT_MODEL = "gpt-4.1-mini"
CONFIG_BATCH_ID = "translation_lt_batch_id"
CONFIG_LAST_RUN = "translation_lt_last_run"

# Žvejybos domeno kontekstas – padeda modeliui suprasti terminiją.
SYSTEM_PROMPT = """Tu esi profesionalus vertėjas iš anglų į lietuvių kalbą, specializuojantis žvejybos reikmenų tekstuose.

Kontekstas: prekės yra žvejybos įranga (meškerės, ritės, valai, kabliai, masalai, vobleriai, aksesuarai) iš didmenininkų FOX International, CC Moore, MOSS/Delphin.

Terminų žodynėlis (naudok šias formas):
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

Formatavimas:
- Išvesk JSON objektą su laukais: "name", "base_name", "description", "technical_description" (jei kuris nors tuščias įvesčiai – taip pat tuščias išvestyje).
- Jei tekstas HTML – išlaikyk žymes nepakeistas.
- Teksto tonas: informatyvus, pardaviminis (e-komercija).
- Jei sąrašas – išlaikyk struktūrą; nepersistenk su skyryba."""


def _estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / 4)


def _openai_headers() -> dict:
    key = getattr(settings, "OPENAI_API_KEY", "") or ""
    if not key:
        raise CommandError("OPENAI_API_KEY nenustatytas (.env).")
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


class Command(BaseCommand):
    help = (
        "Verčia offer'ių tekstus į LT per OpenAI Batch API (lėtas, pigus).\n"
        "Eiga: --prepare (sugeneruoja JSONL) -> --submit (įkelia batch'ą) ->\n"
        "--status (patikrina) -> --apply (įrašo atgal į OfferText/ProductText)."
    )

    def add_arguments(self, parser):
        parser.add_argument("--prepare", action="store_true", help="Sugeneruoti JSONL failą")
        parser.add_argument("--submit", action="store_true", help="Pateikti batch'ą OpenAI")
        parser.add_argument("--status", action="store_true", help="Patikrinti batch'o būseną")
        parser.add_argument("--apply", action="store_true", help="Įrašyti vertimus į DB")
        parser.add_argument("--batch-id", help="Ranka nurodytas batch_id")
        parser.add_argument("--model", default=None, help="OpenAI modelis (default iš config)")
        parser.add_argument("--limit", type=int, default=0, help="Riboti eilučių skaičių")
        parser.add_argument("--force", action="store_true", help="Versti viską iš naujo (ignoruoti esamus LT)")
        parser.add_argument("--out", default="translation_lt_batch.jsonl", help="Išvesties failas")

    def handle(self, *args, **options):
        lt = Language.objects.filter(code="lt").first()
        if not lt:
            raise CommandError("Language 'lt' nesukurtas (corecfg).")

        if options["prepare"]:
            self._prepare(lt, options)
        elif options["submit"]:
            self._submit(options)
        elif options["status"]:
            self._status(options)
        elif options["apply"]:
            self._apply(lt, options)
        else:
            raise CommandError("Nurodyk veiksmą: --prepare/--submit/--status/--apply")

    # ---------- prepare ----------

    def _offers_to_translate(self, lt, force: bool):
        qs = Offer.objects.filter(is_published=True).select_related("product")
        if not force:
            qs = qs.exclude(
                id__in=OfferText.objects.filter(language=lt).values_list("offer_id", flat=True)
            )
        return qs

    def _prepare(self, lt, options):
        model = options["model"] or get_config("translation_model", DEFAULT_MODEL)
        offers = list(self._offers_to_translate(lt, options["force"]))
        if options["limit"]:
            offers = offers[: options["limit"]]
        if not offers:
            self.stdout.write("Nieko versti – visi publikuoti offer'iai jau turi LT tekstus.")
            return

        total_tokens = 0
        with open(options["out"], "w", encoding="utf-8") as handle:
            for offer in offers:
                fields = {
                    "name": offer.name or "",
                    "base_name": offer.base_name or "",
                    "description": offer.description or "",
                    "technical_description": offer.technical_description or "",
                }
                if not any(fields.values()):
                    continue
                body = {
                    "model": model,
                    "temperature": 0.2,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {
                            "role": "user",
                            "content": json.dumps(fields, ensure_ascii=False),
                        },
                    ],
                }
                line = {
                    "custom_id": str(offer.id),
                    "method": "POST",
                    "url": "/v1/chat/completions",
                    "body": body,
                }
                handle.write(json.dumps(line, ensure_ascii=False) + "\n")
                total_tokens += (
                    _estimate_tokens(SYSTEM_PROMPT)
                    + _estimate_tokens(json.dumps(fields))
                    + 500  # išvestis ~ panašus dydis
                )

        self.stdout.write(self.style.SUCCESS(
            f"Paruošta {len(offers)} eilučių -> {options['out']}\n"
            f"Modelis: {model}\n"
            f"Apytikslis token'ų įvertis: ~{total_tokens:,}\n"
            f"(tikroji kaina priklauso nuo modelio; Batch API = 50% nuolaida)"
        ))

    # ---------- submit ----------

    def _submit(self, options):
        file_path = options["out"]
        try:
            with open(file_path, "rb") as handle:
                upload = requests.post(
                    FILES_ENDPOINT,
                    headers={"Authorization": f"Bearer {settings.OPENAI_API_KEY}"},
                    files={"file": ("batch.jsonl", handle, "application/jsonl")},
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
                "metadata": {"kind": "translation_lt", "model": model},
            },
            timeout=60,
        )
        batch.raise_for_status()
        batch_id = batch.json()["id"]
        ConfigEntry.objects.update_or_create(
            key=CONFIG_BATCH_ID, defaults={"value": batch_id}
        )
        ConfigEntry.objects.update_or_create(
            key=CONFIG_LAST_RUN,
            defaults={"value": timezone.now().isoformat()},
        )
        self.stdout.write(self.style.SUCCESS(f"Batch pateiktas: {batch_id}"))

    # ---------- status ----------

    def _status(self, options):
        batch_id = options.get("batch_id") or get_config(CONFIG_BATCH_ID, "")
        if not batch_id:
            raise CommandError("Batch ID nerastas (pateik --batch-id arba --submit).")
        response = requests.get(
            f"{BATCH_ENDPOINT}/{batch_id}", headers=_openai_headers(), timeout=30
        )
        response.raise_for_status()
        data = response.json()
        self.stdout.write(
            f"Batch {batch_id}: status={data.get('status')} "
            f"completed={data.get('request_counts', {}).get('completed', 0)}/"
            f"{data.get('request_counts', {}).get('total', 0)} "
            f"failed={data.get('request_counts', {}).get('failed', 0)}"
        )
        if data.get("status") == "completed":
            self.stdout.write(
                f"Output file: {data.get('output_file_id')} (galima --apply)"
            )

    # ---------- apply ----------

    def _apply(self, lt, options):
        batch_id = options.get("batch_id") or get_config(CONFIG_BATCH_ID, "")
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

        ok = errors = 0
        offers = {o.id: o for o in Offer.objects.filter(
            id__in=[int(json.loads(l)["custom_id"]) for l in out.text.splitlines() if l.strip()]
        ).select_related("product")}

        for line in out.text.splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            offer = offers.get(int(row["custom_id"]))
            if not offer:
                errors += 1
                continue
            body = (row.get("response") or {}).get("body") or {}
            choices = body.get("choices") or []
            if not choices:
                errors += 1
                continue
            try:
                content = json.loads(choices[0]["message"]["content"])
            except (json.JSONDecodeError, KeyError, IndexError):
                errors += 1
                continue
            fields = {
                "name": (content.get("name") or "").strip(),
                "base_name": (content.get("base_name") or "").strip(),
                "description": (content.get("description") or "").strip(),
                "technical_description": (content.get("technical_description") or "").strip(),
            }
            text, _ = OfferText.objects.update_or_create(
                offer=offer, language=lt, defaults=fields
            )
            if offer.product_id:
                self._propagate_to_product(offer, lt, fields)
            ok += 1

        self.stdout.write(self.style.SUCCESS(
            f"Įrašyta {ok} LT vertimų (klaidų: {errors})."
        ))

    def _propagate_to_product(self, offer, lt, fields):
        from django.utils.text import slugify

        text = ProductText.objects.filter(
            product_id=offer.product_id, language=lt
        ).first()
        if text is None:
            ProductText.objects.create(
                product_id=offer.product_id,
                language=lt,
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
