#!/usr/bin/env python3
"""Trace the silhouette of a raster mark into editable straight-line SVG paths."""

import argparse
from pathlib import Path
import shutil
import subprocess
import sys

MAX_EDGE = 1024
MOVES = ((1, 0), (0, 1), (-1, 0), (0, -1))


def load_raster(source):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise ValueError("Install FFmpeg and put it on your PATH.")
    command = [
        ffmpeg, "-nostdin", "-v", "error", "-protocol_whitelist", "file,pipe",
        "-i", str(source.resolve()), "-frames:v", "1",
        "-vf", f"scale=w='min(iw,{MAX_EDGE})':h='min(ih,{MAX_EDGE})':force_original_aspect_ratio=decrease",
        "-pix_fmt", "rgba", "-c:v", "pam", "-f", "image2pipe", "pipe:1",
    ]
    result = subprocess.run(command, capture_output=True, timeout=30)
    if result.returncode:
        raise ValueError("FFmpeg could not read the input as an image.")
    header, marker, pixels = result.stdout.partition(b"ENDHDR\n")
    if not marker or not header.startswith(b"P7\n"):
        raise ValueError("FFmpeg did not produce a readable first frame.")
    fields = dict(line.split(maxsplit=1) for line in header.splitlines()[1:])
    width, height = int(fields[b"WIDTH"]), int(fields[b"HEIGHT"])
    if len(pixels) != width * height * 4:
        raise ValueError("FFmpeg returned an incomplete first frame.")
    return width, height, pixels


def foreground(pixels, threshold):
    """Composite transparency over white and return a one-byte ink mask."""
    mask = bytearray(len(pixels) // 4)
    for index in range(len(mask)):
        start = index * 4
        red, green, blue, alpha = pixels[start:start + 4]
        luminance = (red * 2126 + green * 7152 + blue * 722 + 5000) // 10000
        value = 255 - ((255 - luminance) * alpha + 127) // 255
        mask[index] = value < threshold
    return mask


def boundary_edges(mask, width, height):
    """Walk every ink-facing edge clockwise, with ink to its right."""
    edges = {}
    for y in range(height):
        for x in range(width):
            offset = y * width + x
            if not mask[offset]:
                continue
            if y == 0 or not mask[offset - width]:
                edges.setdefault((x, y), set()).add(0)
            if x == width - 1 or not mask[offset + 1]:
                edges.setdefault((x + 1, y), set()).add(1)
            if y == height - 1 or not mask[offset + width]:
                edges.setdefault((x + 1, y + 1), set()).add(2)
            if x == 0 or not mask[offset - 1]:
                edges.setdefault((x, y + 1), set()).add(3)
    return edges


def contours(edges):
    """Follow edges into closed loops, keeping only points where they turn."""
    while edges:
        start = vertex = next(iter(edges))
        direction = min(edges[vertex])
        points = [start]
        while True:
            outgoing = edges[vertex]
            outgoing.remove(direction)
            if not outgoing:
                del edges[vertex]
            dx, dy = MOVES[direction]
            vertex = (vertex[0] + dx, vertex[1] + dy)
            if vertex == start:
                break
            outgoing = edges[vertex]
            next_direction = next(
                option
                for option in ((direction + 1) % 4, direction, (direction - 1) % 4)
                if option in outgoing
            )
            if direction != next_direction:
                points.append(vertex)
            direction = next_direction
        yield points


def build_svg(width, height, loops):
    subpaths = []
    for points in loops:
        x, y = points[0]
        commands = [f"M{x} {y}"]
        for next_x, next_y in points[1:]:
            commands.append(f"H{next_x}" if next_y == y else f"V{next_y}")
            x, y = next_x, next_y
        commands.append("Z")
        subpaths.append(" ".join(commands))
    path = " ".join(subpaths)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}">\n'
        f'  <path fill="currentColor" fill-rule="evenodd" d="{path}"/>\n'
        '</svg>\n'
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Raster input file")
    parser.add_argument("output", type=Path, help="New .svg file to write")
    parser.add_argument("--threshold", type=int, default=160, help="Ink cutoff from 1 to 255 (default 160)")
    args = parser.parse_args()
    if not 1 <= args.threshold <= 255:
        parser.error("--threshold must be between 1 and 255")
    if args.output.suffix.lower() != ".svg":
        parser.error("output must have an .svg extension")
    try:
        width, height, pixels = load_raster(args.source)
        edges = boundary_edges(foreground(pixels, args.threshold), width, height)
        if not edges:
            raise ValueError("The current threshold found no ink in the image.")
        loops = list(contours(edges))
        with args.output.open("x", encoding="utf-8") as output:
            output.write(build_svg(width, height, loops))
    except FileExistsError:
        sys.exit(f"Output already exists: {args.output}")
    except subprocess.TimeoutExpired:
        sys.exit("FFmpeg did not finish reading the input within 30 seconds.")
    except (OSError, ValueError) as error:
        sys.exit(str(error))
    print(f"Wrote {args.output} with {len(loops)} closed contours.")


if __name__ == "__main__":
    main()
