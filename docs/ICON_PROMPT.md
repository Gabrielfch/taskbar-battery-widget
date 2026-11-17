# Make a custom icon with an AI chat

Taskbar Battery Widget can show your own image as the icon of a device type (all mice, all keyboards…) or of
one device: *Settings ▸ Personalization ▸ Custom icons…*, or click the device icon while editing its alias.

Copy one of these prompts into an AI chat, fill in the `[brackets]`, and load the result in the app.

**What the app accepts:** PNG, JPG, BMP, ICO or GIF (up to 2 MB and 2048×2048 px) and SVG (up to 512 KB; it is converted to a
256×256 PNG when you import it, and an SVG is rebuilt from a safe subset: only `path`, `rect`, `circle`, `ellipse`, `line`, `polyline`, `polygon`, `g`, `defs`, gradients and `clipPath`; anything else, such as `style`, `text`, filters, `use`, images, scripts or `<!DOCTYPE>`, makes it be rejected). Use PNG or
SVG for a transparent background.

**Animated icons:** a GIF or WebP also works (up to 2 MB, 48 frames, 15 fps, 10 s). Keep it to a few frames, a short seamless loop and a transparent background; it is converted to a PNG strip on import, and the animation pauses when Animations is set to *Never* or follows Windows with effects off.

## Prompt for a chat that creates images

```text
I need an icon for a Windows app that shows my devices' battery level on the taskbar.

What it shows: [describe the device, e.g. "black wireless gaming mouse with RGB accents", or attach a photo
and write "the device in the attached image"]
Style: [e.g. "flat minimal silhouette" / "Windows 95 style pixel art" / "simplified realistic" / "outline"]

Technical requirements (mandatory):
- Square PNG, 256×256 px, 100 % transparent background (alpha channel), no shadow, no coloured background.
- The object centred and filling about 85 % of the canvas, with a small even margin.
- It must be readable at 16–20 px: simple, recognisable silhouette, few details, no text or letters, thick
  shapes.
- It must look good on a dark taskbar (#202020) and on a light one (#F3F3F3): use colours with good contrast
  or a thin outline that separates the shape from the background.
- One object, front or 3/4 view, no extreme perspective.
- Do not copy logos or trademarks: take inspiration from the shape, not the logo.

Give me the final image plus a preview scaled down to 20×20 px on a dark and on a light background.
```

## Prompt for a chat that only writes code (SVG, then convert to PNG)

```text
Design an icon as SVG code for a Windows app that shows device battery levels on the taskbar.

What it shows: [description, or "the device in the attached image"]
Style: [flat silhouette / outline / retro pixel art...]

Requirements:
- viewBox="0 0 256 256", transparent background. Use ONLY these elements: path, rect, circle, ellipse, line,
  polyline, polygon, g, linearGradient/radialGradient (with stop) and clipPath. No style attribute or <style>, no
  text, filters, use, masks, patterns, images, stroke-dasharray or animation: set colours with the fill/stroke
  attributes.
- Centred shape filling about 85 % of the canvas, simple shapes and strokes of at least 16 units so it reads
  at 16–20 px.
- Good contrast on dark (#202020) and light (#F3F3F3) backgrounds: add a thin outline of the opposite
  colour if needed.
- No logos or trademarks.

Also give me a command to convert it to a 256×256 PNG (optional: the app imports the SVG directly) with a transparent background (for example with
Inkscape: inkscape icon.svg --export-type=png -w 256 -h 256 -o icon.png).
```

## Prompt en español (chat que genera imágenes)

```text
Necesito un icono para una app de Windows que muestra la batería de mis dispositivos en la barra de tareas.

Qué representa: [describí el dispositivo, o adjuntá una foto y escribí "el dispositivo de la imagen adjunta"]
Estilo: [silueta plana minimalista / pixel art estilo Windows 95 / realista simplificado / contorno]

Requisitos técnicos (obligatorios):
- PNG cuadrado de 256×256 px, fondo 100 % transparente, sin sombra ni fondo de color.
- Objeto centrado ocupando cerca del 85 % del lienzo, con un margen pequeño parejo.
- Legible a 16–20 px: silueta simple, pocos detalles, sin texto, formas gruesas.
- Que se vea bien sobre barra oscura (#202020) y clara (#F3F3F3): buen contraste o un borde fino.
- Un solo objeto, vista frontal o 3/4.
- Sin logotipos ni marcas registradas.

Entregá la imagen final y una vista previa a 20×20 px sobre fondo oscuro y claro.
```
