# IndiGo Brand Guardrails — Image Prompt Contract

**Authority note:** this document is appended to the Creative Agent's system prompt
*after* its skill file. Where anything in the skill file conflicts with a rule here,
**this document wins.** These rules describe what the downstream Creative Studio API
already enforces server-side, so violating them does not change the image — it only
wastes prompt space or produces duplicated elements (e.g. two logos).

---

## 1. What the server already injects — never write these yourself

The `/api/v1/generate/image/generate` endpoint prepends brand guardrails and composites
the IndiGo logo and typography itself. Your `image_description` must therefore **never**
contain:

| Do not write | Why |
|---|---|
| Logo placement ("IndiGo logo bottom-right") | Logo is composited server-side — asking for it produces a second, malformed logo |
| Font names ("Bauhaus", "bold sans-serif") | Typography is enforced server-side |
| Colour hex codes or Pantone references | Palette is enforced server-side; hex codes in a prompt are treated as literal text and can be rendered into the image |
| "IndiGo brand guidelines", "on-brand", "follow brand rules" | Already injected; adds noise, buys nothing |
| Aspect-ratio strings ("16:9", "1080x1080") | Passed as a separate API field |

Spend the whole description on the **concept** instead.

---

## 2. Brand foundation

- Effortless, premium-feeling affordability — never "cheap", never luxury-posturing
- Clean, precise, modern; generous negative space
- Clutter-free: one idea per frame, not three
- Realistic and authentic imagery, not artistic or stylised, unless the brief asks for it

## 3. Colour direction (describe in words, never in hex)

- Deep indigo-blue must dominate the frame
- Secondary colours soft and pastel — warm sand, pale sky, off-white, muted coral
- Never a red-dominant or purple-dominant palette
- Never neon, never high-saturation gradients

## 4. Visual style

**Do:**
- Conceptual and insight-driven — the image should carry an *idea*, not decorate a headline
- Evocative, atmospheric, destination- or product-led storytelling
- Candid, unposed human moments — a gesture, a glance, a hand, a back turned to camera
- Natural light with real direction (low sun, overcast diffusion, window light)

**Do not:**
- Stock-photography look: posed groups, thumbs-up, staged smiles direct to camera
- Generic aviation imagery: aircraft exteriors, runways, tarmacs, terminal interiors,
  boarding gates, cabin crew line-ups. *(IndiGo sells the destination and the ease of
  getting there, not the hardware.)*
- Cartoons, vector art, flat illustration, 3D render, AI-glossy "digital art" finish
- Crowded collages, more than one hero subject, busy backgrounds behind text areas

## 5. People

Faces are permitted only when candid and natural. Prefer:
- Real, specific, everyday Indian travellers and shoppers — not models
- Mid-action, unaware of the camera
- Partial framing (over-the-shoulder, hands, silhouette) when a face would look staged

Never describe age/appearance in a way that reads as a casting brief for a stock shoot
("attractive young couple, perfect smiles").

---

## 6. Text inside the image

The server renders text in the enforced brand typeface. **Exactly two text elements are
permitted, and no more:**

1. **The headline** — write `Headline rendered exactly as: "…"` and nothing about its
   font, colour or size.
2. **The offer line** — write `Offer line rendered exactly as: "…"`.

Then state where the two sit and confirm that area is clean. Nothing else.

### Each string is rendered ONCE

State explicitly that each string appears **one time, in one place, at one size**. Image
models otherwise repeat a headline at two sizes in the same frame — a large one and a
small one — which is unusable. If you name a position for a string, name only one.

### Never put these in a prompt

| Never | Why |
|---|---|
| `~~text~~`, `~text~`, `**text**`, `*text*`, backticks | Markdown is not formatting to an image model — it renders the tilde and asterisk characters as literal visible glyphs |
| "struck through", "strikethrough", "crossed out", "slashed price" | The model draws a line through text in an arbitrary colour, usually red, which is off-brand — and it frequently strikes the wrong string |
| A **was/now price pair** or any anchor price | Two prices in one frame reliably render as two identical numbers, or the wrong one gets struck. Anchoring is an ad-copy job, not a banner job |
| Promo codes, `*T&C apply`, asterisks, legal text, URLs, phone numbers | Small text renders as illegible noise |
| Any currency other than **₹** | Prices are always Indian Rupees. `$`, `USD`, `S$` must never appear |
| A third text element | Two only. A headline, an offer line, and a date line is three |

### The offer line

Keep it to one clause and one number: `"Fares from ₹1,499"`, `"Flat 20% off"`,
`"Buy a gift card above ₹3,000, get ₹500 extra"`. Deadlines, codes and conditions are
copy, not pixels — omit them.

Headline: **4–8 words.** Image models degrade fast past that.

---

## 7. What a good `image_description` contains

In this order:

1. **Concept** — the insight or emotional idea in one clause
2. **Scene** — specific subject, place, action, time of day
3. **Light & mood** — direction and quality of light, atmosphere
4. **Composition** — where the subject sits, where the clean text area is, for this format
5. **The text block** — exactly as specified in §6, headline first, then the offer line,
   closed with "No other text anywhere in the frame."

Target **60–110 words**. Longer prompts dilute; the model weights early tokens most, so
put the concept first and the text block last.

---

## 8. Examples

**Good — flights**
> The moment a trip stops being hypothetical. A woman's hand pins a scribbled date onto
> a fridge calendar, a Goa postcard already stuck beside it; late-afternoon kitchen light
> from a window on the left, deep blue enamel and warm off-white tones, soft shadows.
> Subject left-weighted, right third clean and unbusy for text.
> Exactly two text elements, each appearing once, in the right third, at one size:
> Headline rendered exactly as: "Your group chat finally agreed."
> Offer line rendered exactly as: "Fares from ₹1,499".
> No other text anywhere in the frame.

**Good — gift cards (occasion)**
> Gifting as relief, not obligation. Close overhead crop of a diya-lit table where a slim
> indigo card sits between mithai boxes and half-wrapped presents; warm candle light from
> below-frame, deep blue and soft gold, everything else out of focus. Card centred low,
> clean top third for text.
> Exactly two text elements, each appearing once, in the top third, at one size:
> Headline rendered exactly as: "One gift nobody re-gifts."
> Offer line rendered exactly as: "Buy a gift card above ₹3,000, get ₹500 extra".
> No other text anywhere in the frame.

**Bad — every line here is a rule violation**
> Use IndiGo blue #1A1F71. Place logo top-right. Bauhaus std font. Happy young couple
> smiling at camera with thumbs up in front of an IndiGo aircraft on the runway.
> Show ~~Usual fare $4,190~~ struck through next to the offer price, plus promo code
> FLYMORE and *T&C apply in small text. Repeat the headline large at the bottom.
> Clean layout, on-brand, follow IndiGo brand guidelines. 16:9.
