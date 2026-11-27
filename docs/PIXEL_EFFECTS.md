# Pixel interaction research and application

The portal retains neutral Light / Dark / System surfaces and amber #FFBF00.
The original octopus images are preserved. The previous global offset shadows,
square treatment of every control and bouncing logo have been removed.

## References inspected

- [8bitcn button](https://www.8bitcn.com/docs/components/button): retro borders,
  component variants and a normal-font option suitable for adapting selectively.
- [8bitcn card](https://www.8bitcn.com/docs/components/card): pixel component anatomy.
- [NES.css](https://nostalgic-css.github.io/NES.css/): CSS component treatment;
  components and page layout remain separate concerns.
- [MDN border-image](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/Properties/border-image):
  sliced borders and the need for a real border fallback.
- [MDN steps](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/Values/easing-function/steps):
  discrete timing instead of smoothing a retro interaction.
- [MDN image-rendering](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/Properties/image-rendering):
  scaling raster artwork; this is not a filter for the entire UI or text.
- [MDN reduced motion](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/At-rules/%40media/prefers-reduced-motion):
  detect and reduce nonessential motion, including pseudo-elements.

## Applied decisions

These are project-specific design decisions, not claims from the sources:

- Primary action buttons use an original 16px SVG border sliced into thin stepped
  corners. No clipping is applied to the button, so focus outlines remain visible.
- A brief, single glint appears on pointer hover. Press feedback moves one pixel.
- Model cards stay flat and readable. Two amber corner brackets appear on hover
  or keyboard focus; they do not translate the card or cover report text.
- Navigation has a small static pixel marker only on the active item.
- Three square dots animate only while a report is loading. They are decorative
  inside the existing status region and represent no measured progress percentage.
- No page-wide animation, scanlines, custom cursor, particle canvas or retro font
  is applied to report text. Standard tables, forms and dialogs remain readable.
- Reduced motion disables animation, transitions and press movement, including
  pseudo-elements. High-contrast mode uses a solid button border fallback.

Implementation: `frontend/src/pixel-effects.css`; neutral color and surface rules
remain in `frontend/src/brand.css`. Existing Radix/shadcn-style primitives remain
in use; no additional UI framework, runtime animation package or font was installed.
