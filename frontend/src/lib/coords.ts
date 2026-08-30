import type { PageViewport } from 'pdfjs-dist'

export type Bbox = [number, number, number, number] // [x0, top, x1, bottom]

export interface ViewportRect {
  left: number
  top: number
  width: number
  height: number
}

/**
 * Maps a Phase 1 bbox to a CSS rect within a PDF.js-rendered page canvas.
 *
 * The classic bug here: three coordinate systems disagree about where "up"
 * is.
 *   - Raw PDF space: origin bottom-left, y increases UPWARD.
 *   - Our bbox (x0, top, x1, bottom), inherited from pdfplumber: origin
 *     top-left, y increases DOWNWARD - i.e. already flipped to match
 *     normal screen/image conventions (see design/DESIGN.md "Span schema").
 *   - PDF.js's rendered viewport: also top-left origin, y increases
 *     downward (matches the canvas it draws into) - but PDF.js's own
 *     `convertToViewportPoint` expects INPUT coordinates in raw PDF space
 *     and applies the flip itself. Feeding our already-flipped bbox
 *     straight into it would flip it a SECOND time, mirroring the overlay
 *     to the wrong vertical position (verified: doing that with the page-1
 *     headline bbox lands it near the bottom of the page instead of the
 *     top).
 *
 * The fix: un-flip our bbox back to raw PDF space (`pageHeightPt - y`)
 * before handing it to PDF.js's own transform, so PDF.js's rotation/scale
 * handling is used correctly instead of hand-rolling a scale-only
 * shortcut that would silently break under page rotation.
 */
export function bboxToViewportRect(bbox: Bbox, pageHeightPt: number, viewport: PageViewport): ViewportRect {
  const [x0, top, x1, bottom] = bbox
  const rawY0 = pageHeightPt - bottom
  const rawY1 = pageHeightPt - top

  const [vx0, vy0] = viewport.convertToViewportPoint(x0, rawY0)
  const [vx1, vy1] = viewport.convertToViewportPoint(x1, rawY1)

  return {
    left: Math.min(vx0, vx1),
    top: Math.min(vy0, vy1),
    width: Math.abs(vx1 - vx0),
    height: Math.abs(vy1 - vy0),
  }
}

/** Union bbox of one or more bboxes - used for an article's overlay
 * highlight, computed as the union of its units' bboxes. */
export function unionBbox(bboxes: Bbox[]): Bbox {
  if (bboxes.length === 0) {
    throw new Error('unionBbox called with no bboxes')
  }
  let [x0, top, x1, bottom] = bboxes[0]
  for (const b of bboxes.slice(1)) {
    x0 = Math.min(x0, b[0])
    top = Math.min(top, b[1])
    x1 = Math.max(x1, b[2])
    bottom = Math.max(bottom, b[3])
  }
  return [x0, top, x1, bottom]
}
