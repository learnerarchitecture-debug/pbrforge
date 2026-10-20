    def _heal_axis(self, images: dict[str, Image], horizontal: bool,
                   band: int) -> tuple[float, float]:
        """Apply one band heal pass on one axis. Returns (peak_diff, max_added_slope)."""
        w = images[next(iter(images))].width
        h = images[next(iter(images))].height
        if band <= 0 or band >= min(w, h):
            return 0.0, 0.0

        peak_diff = 0.0
        max_added_slope = 0.0

        for kind, img in images.items():
            if img.channels == 1:
                plane = img.plane(0)
            else:
                plane = img.luminance().plane(0)

            if horizontal:
                # Compute seam difference for each row: diff = right_edge - left_edge
                diffs = [plane[y * w + w - 1] - plane[y * w] for y in range(h)]
                peak_diff = max(peak_diff, max(abs(d) for d in diffs))
                max_added_slope = max(max_added_slope, max(abs(d) for d in diffs) / (band - 1) if band > 1 else 0.0)

                # Apply correction to left band (columns 0 to band-1)
                # Correction goes from full diff at seam (x=0) to 0 at band-1
                data = list(img.data)
                ch = img.channels
                for y in range(h):
                    diff = diffs[y]
                    if abs(diff) < 1e-10:
                        continue
                    row_base = y * w * ch
                    for x in range(band):
                        if band > 1:
                            t = x / (band - 1)
                        else:
                            t = 1.0
                        correction = diff * (1 - t)  # full diff at x=0, 0 at band-1
                        for c in range(ch):
                            idx = (y * w + x) * ch + c
                            data[idx] += correction
                    img = Image(w, h, ch, img.space, data)
                    images[kind] = img
            else:
                # Vertical heal: seam is between top (y=0) and bottom (y=h-1)
                # Compute diff for each column: bottom - top
                diffs = [plane[(h - 1) * w + x] - plane[x] for x in range(w)]
                peak_diff = max(peak_diff, max(abs(d) for d in diffs))
                max_added_slope = max(max_added_slope, max(abs(d) for d in diffs) / (band - 1) if band > 1 else 0.0)

                data = list(img.data)
                ch = img.channels
                for x in range(w):
                    diff = diffs[x]
                    if abs(diff) < 1e-10:
                        continue
                    for y in range(band):
                        if band > 1:
                            t = y / (band - 1)
                        else:
                            t = 1.0
                        correction = diff * (1 - t)  # full diff at y=0, 0 at band-1
                        for c in range(ch):
                            idx = (y * w + x) * ch + c
                            data[idx] += correction
                    img = Image(w, h, ch, img.space, data)
                    images[kind] = img

        # Steepest slope of the linear ramp = peak_diff / (band - 1)
        max_added_slope = max_added_slope
        return peak_diff, max_added_slope
