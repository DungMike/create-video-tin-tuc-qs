/**
 * Geometry for placing a Story Video overlay (waveform, CTA) on the output frame.
 *
 * Mirrors `src/utils/overlay_placement.py` exactly — inside the frame, rounded
 * down to an even offset — because a position the editor lets you drop the
 * overlay on, but the backend would then move, is a preview that lies.
 */
export const FRAME_W = 1920;
export const FRAME_H = 1080;

export type OverlayCorner = "top_left" | "top_right" | "bottom_left" | "bottom_right";

export interface PlacementBox {
  /** Stable key + the label shown when the overlay artwork cannot be drawn. */
  label: string;
  /** Free coordinates, or null while this overlay is still on a corner preset. */
  x: number | null;
  y: number | null;
  /** Corner preset, used to place the box while x/y are null. */
  position: OverlayCorner;
  margin: number;
  width: number;
  height: number;
  /** The processed alpha MOV, drawn inside the box so placement is WYSIWYG. */
  previewPath?: string;
  className: string;
  active?: boolean;
}

function evenDown(value: number): number {
  const rounded = Math.round(value);
  return rounded - (rounded % 2);
}

export function clampPlacement(x: number, y: number, width: number, height: number) {
  return {
    x: evenDown(Math.max(0, Math.min(x, Math.max(0, FRAME_W - width)))),
    y: evenDown(Math.max(0, Math.min(y, Math.max(0, FRAME_H - height)))),
  };
}

/** Where a corner preset puts the overlay, in the same free coordinates. */
export function cornerToPlacement(
  corner: OverlayCorner,
  margin: number,
  width: number,
  height: number,
) {
  const x = corner === "top_left" || corner === "bottom_left" ? margin : FRAME_W - width - margin;
  const y = corner === "top_left" || corner === "top_right" ? margin : FRAME_H - height - margin;
  return clampPlacement(x, y, width, height);
}

/** Resolved top-left of a box, whether it is on free coordinates or a corner. */
export function boxOrigin(box: PlacementBox) {
  if (box.x !== null && box.y !== null) {
    return clampPlacement(box.x, box.y, box.width, box.height);
  }
  return cornerToPlacement(box.position, box.margin, box.width, box.height);
}
