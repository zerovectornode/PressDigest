// @vitest-environment node
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import * as pdfjs from 'pdfjs-dist/legacy/build/pdf.mjs'
import { describe, expect, it } from 'vitest'
import { bboxToViewportRect, unionBbox, type Bbox } from './coords'

const here = path.dirname(fileURLToPath(import.meta.url))
const PDF_PATH = path.resolve(here, '../../../docs/Newspaper.pdf')

// Real page-1 headline bbox from Phase 1 bronze data (span p001-s00056,
// text "Karki is Nepal's first woman PM"), in our top-down (x0, top, x1,
// bottom) convention - see design/DESIGN.md "Coordinate mapping".
const HEADLINE_BBOX: Bbox = [265.75, 336.30343659999994, 834.549691667294, 378.1468365999999]
const PAGE_WIDTH_PT = 992.13
const PAGE_HEIGHT_PT = 1530.71

// Real page-1 body line from the Nepal article (L0100, bronze layer,
// data/bronze/delhi/2025-09-13/page_01/page.json - not a headline/drop-cap,
// an ordinary body-text line partway down column 3), so the fixture also
// covers a plain multi-rect body fragment rather than only the one
// large/bold headline case above.
const BODY_LINE_BBOX: Bbox = [384.45172560000003, 538.5148778, 488.9900597746205, 547.4812278]

async function loadPage1Viewport(scale = 1) {
  const data = new Uint8Array(readFileSync(PDF_PATH))
  const doc = await pdfjs.getDocument({ data }).promise
  const page = await doc.getPage(1)
  return page.getViewport({ scale })
}

describe('bboxToViewportRect', () => {
  it('places the page-1 headline near the top of the page, not mirrored to the bottom', async () => {
    const viewport = await loadPage1Viewport(1)
    const rect = bboxToViewportRect(HEADLINE_BBOX, PAGE_HEIGHT_PT, viewport)

    // The headline sits at top-down y ~336-378 out of a 1530.71pt-tall
    // page (~22-25% down from the top). A mirrored/flipped bug would place
    // it at ~1152-1194 instead (~75-78% down) - assert it's in the top
    // third, nowhere near the bottom.
    expect(rect.top).toBeCloseTo(336.30343659999994, 3)
    expect(rect.top + rect.height).toBeCloseTo(378.1468365999999, 3)
    expect(rect.top / viewport.height).toBeLessThan(0.3)

    // Horizontal placement is untouched by the flip and should pass through unchanged.
    expect(rect.left).toBeCloseTo(265.75, 3)
    expect(rect.width).toBeCloseTo(834.549691667294 - 265.75, 3)
  })

  it('scales proportionally with the viewport scale factor', async () => {
    const viewport1x = await loadPage1Viewport(1)
    const viewport2x = await loadPage1Viewport(2)

    const rect1x = bboxToViewportRect(HEADLINE_BBOX, PAGE_HEIGHT_PT, viewport1x)
    const rect2x = bboxToViewportRect(HEADLINE_BBOX, PAGE_HEIGHT_PT, viewport2x)

    expect(rect2x.left).toBeCloseTo(rect1x.left * 2, 3)
    expect(rect2x.top).toBeCloseTo(rect1x.top * 2, 3)
    expect(rect2x.width).toBeCloseTo(rect1x.width * 2, 3)
    expect(rect2x.height).toBeCloseTo(rect1x.height * 2, 3)
  })

  it('places a real body-text line rect correctly, not just the headline', async () => {
    const viewport = await loadPage1Viewport(1)
    const rect = bboxToViewportRect(BODY_LINE_BBOX, PAGE_HEIGHT_PT, viewport)

    // L0100 sits at top-down y ~538-547 (~35% down a 1530.71pt page). A
    // mirrored/flipped bug would place it at ~983-992 (~65% down) instead -
    // this is the multi-rect-body case (a small mid-page fragment, not the
    // one large bold headline span already covered above).
    expect(rect.top).toBeCloseTo(538.5148778, 3)
    expect(rect.top + rect.height).toBeCloseTo(547.4812278, 3)
    expect(rect.top / viewport.height).toBeLessThan(0.4)
    expect(rect.left).toBeCloseTo(384.45172560000003, 3)
    expect(rect.width).toBeCloseTo(488.9900597746205 - 384.45172560000003, 3)
  })

  it('round-trips the four page corners without any axis flip at scale 1', async () => {
    const viewport = await loadPage1Viewport(1)
    const fullPage: Bbox = [0, 0, PAGE_WIDTH_PT, PAGE_HEIGHT_PT]
    const rect = bboxToViewportRect(fullPage, PAGE_HEIGHT_PT, viewport)

    expect(rect.left).toBeCloseTo(0, 3)
    expect(rect.top).toBeCloseTo(0, 3)
    expect(rect.width).toBeCloseTo(PAGE_WIDTH_PT, 3)
    expect(rect.height).toBeCloseTo(PAGE_HEIGHT_PT, 3)
  })
})

describe('unionBbox', () => {
  it('computes the min/max envelope of multiple bboxes', () => {
    const a: Bbox = [10, 10, 20, 20]
    const b: Bbox = [5, 30, 25, 40]
    expect(unionBbox([a, b])).toEqual([5, 10, 25, 40])
  })

  it('returns a single bbox unchanged', () => {
    const a: Bbox = [1, 2, 3, 4]
    expect(unionBbox([a])).toEqual(a)
  })

  it('throws on an empty list rather than silently returning a bogus rect', () => {
    expect(() => unionBbox([])).toThrow()
  })
})
