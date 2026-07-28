#!/usr/bin/env python3
"""
Creative agent as a LangGraph SUBGRAPH on the shared CampaignState.

Internal flow:
    plan -> generate -> log_outputs

What it reads from the blackboard:
    run_id          -- DB campaign run id for audit logging (None if DB not configured)
    product         (intake)
    objective       (intake)
    total_budget    (intake)
    brief           (intake) -- full brief for metadata tagging
    channels        (GTM)   -- selected channels with weights
    finance         (Finance) -- per-channel budget allocations

What it writes:
    creative: {
        plans:   [{channel, format, width, height, prompt}]
        outputs: [{channel, format, status, job_id, image_url, error_message}]
        summary: {total, success, failed}
    }
    _creative_plans:   None   (cleared after generate)
    _creative_outputs: None   (cleared after log_outputs)
"""
from __future__ import annotations

import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import httpx
from openai import AzureOpenAI
from langgraph.graph import StateGraph, START, END

from core.state import CampaignState

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
SKILL_FILE       = str(Path(__file__).resolve().parent / "skill.md")
GUARDRAILS_FILE  = str(Path(__file__).resolve().parent.parent.parent / "prompts" / "indigo_brand_guardrails.md")

# Channels that produce image creative; maps to (width, height, format label).
CHANNEL_FORMAT: dict[str, dict] = {
    "Meta":            {"width": 1080, "height": 1080, "label": "square_feed",    "aspect_ratio": "1:1"},
    "YouTube":         {"width": 1920, "height": 1080, "label": "display_banner", "aspect_ratio": "16:9"},
    "Email":           {"width": 600,  "height": 300,  "label": "email_header",   "aspect_ratio": "16:9"},
    "Google Shopping": {"width": 800,  "height": 800,  "label": "product_image",  "aspect_ratio": "1:1"},
    "Affiliates":      {"width": 1200, "height": 628,  "label": "web_banner",     "aspect_ratio": "16:9"},
}

# Fallback order when we need to pad to MIN_VISUAL_CHANNELS.
_VISUAL_CHANNEL_PRIORITY = ["Meta", "YouTube", "Email", "Google Shopping", "Affiliates"]
MIN_VISUAL_CHANNELS = 3

AZURE_OPENAI_DEPLOYMENT  = os.getenv("AZURE_OPENAI_DEPLOYMENT",  "gpt-4.1")
AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2024-05-01-preview")
REQUEST_TIMEOUT = 1000  # seconds per individual Creative Studio call


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _load_skill() -> str:
    body = Path(SKILL_FILE).read_text(encoding="utf-8")
    return re.sub(
        r"^---\n.*?\n---\n",
        "",
        body,
        count=1,
        flags=re.DOTALL,
    ).strip()


def _load_guardrails() -> str:
    try:
        return Path(GUARDRAILS_FILE).read_text(encoding="utf-8").strip()
    except Exception:
        logger.warning("[creative] Could not load brand guardrails from %s", GUARDRAILS_FILE)
        return ""


def _llm() -> AzureOpenAI:
    return AzureOpenAI(
        api_key=os.getenv("AZURE_OPENAI_API_KEY"),
        azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
        api_version=AZURE_OPENAI_API_VERSION,
    )



def _visual_channels(state_channels: list[dict]) -> list[dict]:
    """Filter to only channels that have a Creative Studio image format."""
    return [ch for ch in state_channels if ch.get("name") in CHANNEL_FORMAT]


def _ensure_min_visual_channels(visual_chs: list[dict]) -> list[dict]:
    """Always return exactly MIN_VISUAL_CHANNELS visual channels.

    >3: keep top-3 by weight so we don't overrun API budget.
    <3: pad with lowest-weight supplementary channels from the priority list.
    """
    if len(visual_chs) > MIN_VISUAL_CHANNELS:
        return sorted(visual_chs, key=lambda c: -c.get("weight", 0))[:MIN_VISUAL_CHANNELS]

    if len(visual_chs) < MIN_VISUAL_CHANNELS:
        existing = {ch["name"] for ch in visual_chs}
        result = list(visual_chs)
        for name in _VISUAL_CHANNEL_PRIORITY:
            if len(result) >= MIN_VISUAL_CHANNELS:
                break
            if name not in existing:
                result.append({"name": name, "weight": 0, "priority": "low", "rationale": "supplementary visual channel"})
                existing.add(name)
        return result

    return visual_chs


def _extract_offer(state: CampaignState) -> str:
    """Prefer the GTM strategy pack's offer -- it is the reasoned one -- then the brief."""
    pricing = state.get("pricing") or {}
    if pricing.get("offer"):
        return str(pricing["offer"])

    brief = state.get("brief", {})
    grounded = brief.get("grounded", brief)
    assumed  = brief.get("assumed", {})
    return grounded.get("offer") or assumed.get("offer") or "special fare"


# Tokens that image models render as literal visible glyphs, or that reliably produce
# a red strikethrough / duplicated price. The prompt forbids these, but the LLM still
# emits them occasionally, and one leaked "~~" ruins the banner -- so strip them here too.
_MARKDOWN_NOISE = re.compile(r"(~~|~|\*\*|\*|`|_{2,})")
_STRIKE_PHRASES = re.compile(
    r"\b(struck[- ]through|strike[- ]?through|strikethrough|crossed[- ]out|"
    r"slashed(?: price)?|cross(?:ed)? through)\b",
    re.IGNORECASE,
)
# A whole foreign-currency AMOUNT: "$4,190", "USD 4190", "S$120", "€99".
# Deliberately removed rather than converted to a rupee symbol -- swapping "$4,190"
# for "₹4,190" would put a plausible but completely wrong price on a customer-facing
# banner, which is worse than dropping the token.
_FOREIGN_AMOUNT = re.compile(
    r"(?:\b(?:USD|SGD|EUR|GBP|S\$)\s?|(?<![A-Za-z0-9])[$€£])\s?\d[\d,.]*",
    re.IGNORECASE,
)


def _sanitise_prompt(text: str) -> str:
    """Strip the tokens that visibly corrupt a generated banner.

    Every rule here corresponds to a defect seen in real output: markdown
    rendered as literal tildes/asterisks, red strike-through lines, and dollar
    amounts in a rupee campaign.

    This is a safety net, not the primary control -- the prompt forbids all of
    it. Removing a strike-through phrase does not remove the anchor price it
    referred to, so the prompt rules still have to do the real work.
    """
    if not text:
        return text

    cleaned = _MARKDOWN_NOISE.sub("", text)
    cleaned = _STRIKE_PHRASES.sub("", cleaned)
    cleaned = _FOREIGN_AMOUNT.sub("", cleaned)
    # Collapse whitespace and orphaned punctuation left behind by the removals.
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\s+([,.;:])", r"\1", cleaned)
    cleaned = re.sub(r"([,;:])\s*(?=[,.;:])", "", cleaned)

    if cleaned != text:
        logger.info("[creative] Sanitised banned tokens from prompt text")
    return cleaned.strip()


def _short_product_label(product: str) -> str:
    """A few words that can safely be rendered on a banner.

    Product strings arrive long and technical -- "IndiGo Gift Cards -- digital and
    physical, denominations Rs1,000 to Rs25,000". Slicing that to a character
    budget leaves a truncated word and a half-written price on the artwork, so cut
    at the first clause boundary and then at a word boundary instead.
    """
    text = _sanitise_prompt(product or "").strip()
    if not text:
        return ""
    # First clause only: stop at an em/en dash, comma, colon, semicolon or bracket.
    clause = re.split(r"\s*[—–,;:(]\s*", text, maxsplit=1)[0].strip()
    words = clause.split()
    return " ".join(words[:6]).rstrip(".,;:-")


_ARCHETYPE_BRIEF = {
    "A": "A -- high-intent transactional. The customer is already searching. Make the "
         "price credible and the deadline felt.",
    "B": "B -- discovery / considered. The customer has not decided they want this yet. "
         "Sell desire through atmosphere and texture.",
    "C": "C -- e-commerce catalog. The customer is browsing a grid. Make one product "
         "tangible: material, scale, in-use context.",
    "D": "D -- gifting / occasion. The buyer is not the user. Show the moment of giving, "
         "not the product.",
}


def _strategy_block(state: CampaignState) -> str:
    """Render the GTM strategy pack fields the Creative agent must design against.

    Without this the creative brief is just product+offer, and the banners come back
    generic -- the anxiety and brand_voice fields are what make them specific.
    """
    positioning = state.get("positioning") or {}
    pricing     = state.get("pricing") or {}
    forces      = positioning.get("forces") or {}
    archetype   = (state.get("archetype") or "").strip()[:1].upper()

    lines: list[str] = []

    if archetype in _ARCHETYPE_BRIEF:
        lines.append(f"Archetype: {_ARCHETYPE_BRIEF[archetype]}")

    if forces.get("anxiety"):
        lines.append(
            f"CUSTOMER ANXIETY (the image must visually defuse this): {forces['anxiety']}"
        )
    if positioning.get("jtbd"):
        lines.append(f"Job to be done (sell this outcome, not the SKU): {positioning['jtbd']}")
    if positioning.get("icp"):
        lines.append(f"Audience: {positioning['icp']}")
    if positioning.get("differentiation"):
        lines.append(f"Differentiation: {positioning['differentiation']}")
    if positioning.get("statement"):
        lines.append(f"Positioning: {positioning['statement']}")

    voice = positioning.get("brand_voice")
    if voice:
        voice_str = ", ".join(voice) if isinstance(voice, list) else str(voice)
        lines.append(f"BRAND VOICE (the headline must sound like this): {voice_str}")

    if pricing.get("anchor"):
        lines.append(f"Price anchor: {pricing['anchor']}")
    if pricing.get("urgency"):
        lines.append(f"Urgency device: {pricing['urgency']}")
    if pricing.get("loyalty") and str(pricing["loyalty"]).lower() not in {"n/a", "none", ""}:
        lines.append(f"Loyalty lever: {pricing['loyalty']}")

    if not lines:
        return ""
    return "CAMPAIGN STRATEGY (design against every line):\n" + "\n".join(f"- {l}" for l in lines)


# --------------------------------------------------------------------------- #
# Nodes
# --------------------------------------------------------------------------- #
def plan(state: CampaignState) -> dict:
    """
    LLM node: turn the GTM strategy pack into one concept + three prompt variants
    per visual channel. Returns _creative_plans consumed by generate.
    """
    channels   = state.get("channels") or []
    visual_chs = _ensure_min_visual_channels(_visual_channels(channels))

    logger.info(
        "[creative/plan] Starting -- visual channels selected: %s",
        [ch["name"] for ch in visual_chs],
    )

    if not visual_chs:
        logger.info("[creative/plan] No visual channels in campaign -- skipping generation.")
        return {"_creative_plans": []}

    product   = state.get("product", "IndiGo campaign")
    objective = state.get("objective", "bookings")
    budget    = state.get("total_budget", 0)
    offer     = _extract_offer(state)

    brief          = state.get("brief", {})
    grounded       = brief.get("grounded", brief)
    assumed        = brief.get("assumed", {})
    creative_brief = (grounded.get("creative_brief") or assumed.get("creative_brief") or "").strip()

    logger.info(
        "[creative/plan] Campaign context -- product=%r offer=%r objective=%r budget=Rs%s creative_brief=%r",
        product, offer, objective, f"{budget:,.0f}", creative_brief or "(none)",
    )

    skill      = _load_skill()
    guardrails = _load_guardrails()
    # Explicit precedence: the two documents overlap, and without this the model
    # resolves conflicts (logo, aviation imagery, hex codes) arbitrarily.
    system_prompt = (
        f"{skill}\n\n"
        "════════════════════════════════════════════════════════════════════════\n"
        "The brand guardrails below OVERRIDE anything above them on conflict.\n"
        "════════════════════════════════════════════════════════════════════════\n\n"
        f"{guardrails}"
    ) if guardrails else skill

    ch_lines = "\n".join(
        f'- {ch["name"]}: {CHANNEL_FORMAT[ch["name"]]["label"]}, '
        f'{CHANNEL_FORMAT[ch["name"]]["width"]}x{CHANNEL_FORMAT[ch["name"]]["height"]} '
        f'({CHANNEL_FORMAT[ch["name"]]["aspect_ratio"]}), weight {ch.get("weight", 0)}'
        for ch in visual_chs
    )

    strategy_block = _strategy_block(state)

    mood_section = (
        f"\nCLIENT VISUAL DIRECTION (non-negotiable):\n{creative_brief}\n"
        if creative_brief else
        "\nNo client visual direction given -- derive the concept from the strategy above.\n"
    )

    user_prompt = (
        f"Campaign product: {product}\n"
        f"Offer (use this literally, do not invent a different price): {offer}\n"
        f"Objective: {objective}\n\n"
        f"{strategy_block}\n"
        f"{mood_section}"
        f"\nChannels to create banners for:\n{ch_lines}\n\n"
        "For EACH channel, produce ONE concept and THREE prompt variants of it, per your "
        "skill and the brand guardrails. Specifically:\n"
        "  1. Name the concept -- the insight the image carries -- before describing any scene.\n"
        "  2. Build the scene so it visually defuses the customer anxiety stated above.\n"
        "  3. Write the headline in the stated brand voice, 4-8 words, with a turn in it.\n"
        "  4. Compose for that channel's exact format and leave the text area clean.\n"
        "  5. Write NO logo placement, NO font names, NO hex codes, NO aspect-ratio strings "
        "-- all are enforced server-side.\n"
        "  6. Make the three variants genuinely different executions. Change at least three "
        "of: framing, time of day, location, POV, human presence. At least one variant must "
        "show no visible face. Two variants that could be the same photograph is a failure.\n"
        "  7. Put EXACTLY TWO text elements in each prompt -- the headline and the offer "
        "line -- each rendered once, in one place, at one size, using the exact block from "
        "your skill file. State 'No other text anywhere in the frame.'\n"
        "  8. Absolutely no markdown (~~ ~ ** * `), no 'struck through' or 'crossed out', "
        "no anchor or was/now price pair, no promo code, no 'T&C', no asterisks, and no "
        "currency symbol except the rupee sign. These render as literal broken glyphs.\n\n"
        "Return a JSON object exactly as specified in your Output Format section: "
        '{"concepts": [{"channel", "concept", "headline", "offer_line", "variants": [3 strings]}]}. '
        "Use the channel names exactly as listed above. Each variant is 60-110 words."
    )

    logger.info("[creative/plan] Sending prompt to LLM (%s):\n%s", AZURE_OPENAI_DEPLOYMENT, user_prompt)

    def _fallback_plan(ch: dict) -> dict:
        """Product-agnostic fallback. Deliberately contains no aviation imagery,
        no logo instruction and no font/colour directives -- the same guardrails
        the LLM path must honour."""
        fmt       = CHANNEL_FORMAT[ch["name"]]
        anxiety   = ((state.get("positioning") or {}).get("forces") or {}).get("anxiety", "")
        direction = creative_brief or "calm, uncluttered, quietly aspirational"
        position  = "top third" if fmt["aspect_ratio"] == "1:1" else "right third"
        headline = _short_product_label(str(product)) or "Somewhere worth going."
        # The offer is left whole -- "Buy a gift card above Rs3,000, get Rs500 extra"
        # loses its meaning if clipped at the comma.
        offer_line = _sanitise_prompt(str(offer))

        def _build(scene: str) -> str:
            return _sanitise_prompt(
                f"{scene} {direction}. Natural directional light with soft shadows, deep blue "
                f"and warm neutral tones, generous negative space, nothing staged. "
                f"Subject placed away from the {position}, which stays clean and uncluttered."
                + (f" The scene should quietly answer the worry that {anxiety}." if anxiety else "")
                + f" Exactly two text elements, each appearing once, in the {position}, at one size: "
                + f'Headline rendered exactly as: "{headline}" '
                + f'Offer line rendered exactly as: "{offer_line}" '
                + "No other text anywhere in the frame."
            )

        # Three deliberately different scenes, not one scene reworded.
        variants = [
            _build(f"A single quiet moment that makes {product} feel already decided. "
                   "Wide environmental frame at dawn, one figure small against the setting, seen from a distance."),
            _build(f"The small private gesture that comes just before choosing {product}. "
                   "Tight macro crop on two hands and the object, indoor window light at night, no face visible."),
            _build(f"The everyday surface where a decision about {product} actually gets made. "
                   "Overhead flat-lay on a worn table in late afternoon, no people in frame at all."),
        ]
        return {
            "channel":    ch["name"],
            "format":     fmt["label"],
            "width":      fmt["width"],
            "height":     fmt["height"],
            "concept":    f"Fallback concept for {product}",
            "headline":   headline,
            "offer_line": offer_line,
            "variants":   variants,
            "prompt":     variants[0],
        }

    plans: list[dict] = []
    try:
        client = _llm()
        resp = client.chat.completions.create(
            model=AZURE_OPENAI_DEPLOYMENT,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ],
            temperature=0.8,          # creative task -- reward variance across variants
            response_format={"type": "json_object"},
            max_completion_tokens=4000,  # 3 channels x 3 variants x ~110 words
        )
        raw = resp.choices[0].message.content or ""
        logger.info("[creative/plan] LLM response received (%d chars):\n%s", len(raw), raw)

        json_match = re.search(r"\{.*\}", raw, re.DOTALL)
        if json_match:
            parsed = json.loads(json_match.group())
            for item in parsed.get("concepts", []):
                ch_name = item.get("channel", "")
                fmt     = CHANNEL_FORMAT.get(ch_name, {})
                if not fmt:
                    logger.warning("[creative/plan] LLM returned unknown channel %r -- skipping", ch_name)
                    continue

                variants = [
                    _sanitise_prompt(v)
                    for v in (item.get("variants") or [])
                    if isinstance(v, str) and v.strip()
                ]
                variants = [v for v in variants if v]
                if not variants:
                    logger.warning("[creative/plan] %r returned no usable variants -- skipping", ch_name)
                    continue

                # Identical variants waste generation budget and give the reviewer
                # nothing to choose between -- drop duplicates before padding.
                deduped: list[str] = []
                for v in variants:
                    if v not in deduped:
                        deduped.append(v)
                if len(deduped) < len(variants):
                    logger.warning(
                        "[creative/plan] %s returned %d duplicate variant(s)",
                        ch_name, len(variants) - len(deduped),
                    )
                variants = deduped

                # Pad/trim to exactly IMAGES_PER_CHANNEL so generate() is predictable.
                supplied = list(variants)
                while len(variants) < IMAGES_PER_CHANNEL:
                    variants.append(supplied[len(variants) % len(supplied)])
                variants = variants[:IMAGES_PER_CHANNEL]

                plans.append({
                    "channel":    ch_name,
                    "format":     fmt.get("label", "banner"),
                    "width":      fmt.get("width",  1200),
                    "height":     fmt.get("height", 628),
                    "concept":    item.get("concept", ""),
                    "headline":   _sanitise_prompt(item.get("headline", "")),
                    "offer_line": _sanitise_prompt(item.get("offer_line", "")),
                    "variants":   variants,
                    "prompt":     variants[0],   # back-compat: single representative prompt
                })
                logger.info(
                    "[creative/plan] %s (%s) | concept=%r headline=%r | %d variants",
                    ch_name, fmt.get("label"), item.get("concept"), item.get("headline"), len(variants),
                )
        else:
            logger.warning("[creative/plan] No JSON object found in LLM response -- will use fallbacks")
    except Exception:
        logger.exception("[creative/plan] LLM call failed -- falling back to template prompts.")

    # Guarantee every visual channel has a plan regardless of LLM output.
    planned = {p["channel"] for p in plans}
    for ch in visual_chs:
        if ch["name"] not in planned:
            logger.warning("[creative/plan] Channel %r missing from LLM response -- injecting fallback plan", ch["name"])
            plans.append(_fallback_plan(ch))

    logger.info(
        "[creative/plan] Done -- %d plans ready: %s",
        len(plans), [p["channel"] for p in plans],
    )
    return {"_creative_plans": plans}


IMAGES_PER_CHANNEL = 3


def _generate_for_plan(
    p: dict,
    endpoint: str,
    project_id: str,
    api_key: str,
) -> dict:
    """Generate one image per prompt variant for a channel.

    Each variant is a genuinely different execution of the same concept, so the
    reviewer gets three real options rather than three renders of one idea.
    """
    fmt          = CHANNEL_FORMAT.get(p["channel"], {})
    aspect_ratio = fmt.get("aspect_ratio", "16:9")

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key

    image_urls: list[str] = []
    used_prompts: list[str] = []
    errors: list[str] = []

    variants = p.get("variants") or [p["prompt"]]

    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
            for image_index, variant_prompt in enumerate(variants, start=1):
                payload = {
                    "image_description": variant_prompt,
                    # Custom dimensions win server-side and give exact pixel output.
                    # aspect_ratio is kept as a fallback for the presets we do match --
                    # Email (2:1) and Affiliates (1.91:1) have no preset equivalent.
                    "aspect_ratio": aspect_ratio,
                    "custom_width":  p["width"],
                    "custom_height": p["height"],
                    "resolution": "2K",
                    "image_count": 1,
                }

                if project_id:
                    payload["project_id"] = project_id

                try:
                    response = client.post(
                        endpoint,
                        json=payload,
                        headers=headers,
                    )
                    response.raise_for_status()
                    body = response.json()

                    images = body.get("images") or body.get("data") or []
                    if not images:
                        errors.append(
                            f"Image {image_index}: no image returned"
                        )
                        continue

                    image = images[0]

                    if isinstance(image, str):
                        image_value = image
                    elif isinstance(image, dict):
                        image_value = (
                            image.get("b64_json")
                            or image.get("base64")
                            or image.get("image_base64")
                            or image.get("url")
                            or image.get("image_url")
                        )
                    else:
                        image_value = None

                    if not image_value:
                        errors.append(
                            f"Image {image_index}: invalid response"
                        )
                        continue

                    if image_value.startswith(("http://", "https://")):
                        image_urls.append(image_value)
                    else:
                        image_urls.append(
                            f"data:image/png;base64,{image_value}"
                        )
                    # Keep the prompt that actually produced this image, so the
                    # audit row in mos_creative_outputs is truthful per variant.
                    used_prompts.append(variant_prompt)

                except httpx.HTTPStatusError as exc:
                    errors.append(
                        f"Image {image_index}: HTTP "
                        f"{exc.response.status_code} - "
                        f"{exc.response.text[:200]}"
                    )
                except Exception as exc:
                    errors.append(
                        f"Image {image_index}: {exc}"
                    )

    except Exception as exc:
        errors.append(str(exc))

    return {
        "channel": p["channel"],
        "image_urls": image_urls,
        "used_prompts": used_prompts,
        "error": "; ".join(errors) if errors else None,
    }

def generate(state: CampaignState) -> dict:
    """
    API node: fire one request per prompt variant, channels in parallel.
    Keeps concurrency at 1 per channel rather than all variants at once.
    Never raises -- failed channels are recorded with status='error'.
    """
    plans      = state.get("_creative_plans") or []
    base_url   = os.getenv("CREATIVE_STUDIO_BASE", "").rstrip("/")
    project_id = os.getenv("CREATIVE_STUDIO_PROJECT_ID", "")
    api_key    = os.getenv("CREATIVE_STUDIO_API_KEY", "")
    endpoint   = f"{base_url}/api/v1/generate/image/generate"

    logger.info(
        "[creative/generate] Starting -- %d channels | endpoint=%s | api_key=%s",
        len(plans), endpoint, "set" if api_key else "NOT SET",
    )

    if not plans:
        logger.info("[creative/generate] No plans -- skipping.")
        return {"_creative_outputs": []}

    if not base_url:
        logger.error("[creative/generate] CREATIVE_STUDIO_BASE is not configured -- skipping all generation.")
        return {"_creative_outputs": [
            {**_empty_out(p), "error_message": "CREATIVE_STUDIO_BASE not configured"}
            for p in plans
        ]}

    if not api_key:
        logger.warning("[creative/generate] CREATIVE_STUDIO_API_KEY is not set -- requests will be rejected with 401")

    # One request per channel, all channels in parallel.
    channel_results: list[dict] = [{}] * len(plans)

    with ThreadPoolExecutor(max_workers=len(plans)) as pool:
        future_to_idx = {
            pool.submit(_generate_for_plan, p, endpoint, project_id, api_key): idx
            for idx, p in enumerate(plans)
        }
        for future in as_completed(future_to_idx):
            channel_results[future_to_idx[future]] = future.result()

    outputs: list[dict] = []
    for p, result in zip(plans, channel_results):
        out = _empty_out(p)
        image_urls = result.get("image_urls", [])
        out["image_urls"]   = image_urls
        out["image_url"]    = image_urls[0] if image_urls else None
        out["used_prompts"] = result.get("used_prompts") or []
        if image_urls:
            out["status"] = "success"
            logger.info(
                "[creative/generate] OK %s (%s) -- %d/%d images",
                p["channel"], p["format"], len(image_urls), IMAGES_PER_CHANNEL,
            )
        else:
            out["error_message"] = result.get("error") or "No images in response"
            logger.error(
                "[creative/generate] x %s (%s) -- 0 images. error=%s",
                p["channel"], p["format"], out["error_message"],
            )
        outputs.append(out)

    success = sum(1 for o in outputs if o.get("status") == "success")
    logger.info(
        "[creative/generate] Complete -- %d/%d channels succeeded",
        success, len(outputs),
    )
    return {"_creative_outputs": outputs}


def _empty_out(p: dict) -> dict:
    return {
        "channel":       p["channel"],
        "format":        p["format"],
        "width":         p["width"],
        "height":        p["height"],
        "prompt":        p["prompt"],
        "concept":       p.get("concept", ""),
        "headline":      p.get("headline", ""),
        "offer_line":    p.get("offer_line", ""),
        "variants":      p.get("variants") or [p["prompt"]],
        "used_prompts":  [],
        "status":        "error",
        "job_id":        None,
        "image_url":     None,
        "image_urls":    [],
        "raw_response":  None,
        "error_message": None,
        "token_scope":   None,
    }


def log_outputs(state: CampaignState) -> dict:
    """
    Assemble the final creative dict, write rows to mos_creative_outputs (best-effort),
    and clear the intermediate working keys.
    """
    outputs  = state.get("_creative_outputs") or []
    plans    = state.get("_creative_plans")    or []
    run_id   = state.get("run_id")
    brief    = state.get("brief", {})

    successful_channels = sum(
        1
        for output in outputs
        if output.get("status") == "success"
    )
    failed_channels = len(outputs) - successful_channels
    total_images = sum(
        len(output.get("image_urls") or [])
        for output in outputs
    )

    # DB logging -- best-effort, never raises
    if run_id is not None:
        try:
            from core.db import log_creative_output
            for output in outputs:
                image_urls   = output.get("image_urls") or []
                used_prompts = output.get("used_prompts") or []

                if image_urls:
                    for variant_index, image_url in enumerate(
                        image_urls,
                        start=1,
                    ):
                        # Log the variant prompt that actually produced this image,
                        # not the channel's representative prompt.
                        variant_prompt = (
                            used_prompts[variant_index - 1]
                            if variant_index <= len(used_prompts)
                            else output.get("prompt")
                        )
                        log_creative_output(
                            run_id,
                            {
                                **output,
                                "prompt": variant_prompt,
                                "image_url": image_url,
                                "variant_index": variant_index,
                                "campaign_brief": brief,
                            },
                        )
                else:
                    log_creative_output(
                        run_id,
                        {
                            **output,
                            "variant_index": 1,
                            "campaign_brief": brief,
                        },
                    )
        except Exception:
            logger.exception("[creative/log_outputs] DB logging failed.")

    creative = {
        "plans":   plans,
        "outputs": [
            {k: v for k, v in o.items() if k != "raw_response"}
            for o in outputs
        ],
        "summary": {
            "total_channels": len(outputs),
            "successful_channels": successful_channels,
            "failed_channels": failed_channels,
            "total_images": total_images,
            "images_per_channel": IMAGES_PER_CHANNEL,
        },
    }

    return {
        "creative":          creative,
        "_creative_plans":   None,
        "_creative_outputs": None,
    }


# --------------------------------------------------------------------------- #
# Subgraph
# --------------------------------------------------------------------------- #
def build_creative_subgraph():
    g = StateGraph(CampaignState)

    g.add_node("plan",        plan)
    g.add_node("generate",    generate)
    g.add_node("log_outputs", log_outputs)

    g.add_edge(START,        "plan")
    g.add_edge("plan",       "generate")
    g.add_edge("generate",   "log_outputs")
    g.add_edge("log_outputs", END)

    return g.compile()



