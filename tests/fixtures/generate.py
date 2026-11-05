"""Deterministic synthetic fixture generator (Faker pools with fixed seeds).

Every name, address and amount here is synthetic. Nothing is copied from a real record.
"""

from __future__ import annotations

import io
import json
import random
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from fpdf import FPDF
from PIL import Image, ImageDraw, ImageFont

from backend.common.snapshot import (
    TOWN,
    address_pool,
    build_snapshot,
    lender_pool,
    person_pool,
    synthetic_rows,
)

FIXTURE_SEED = 20261105
GENERATED_AT = "2026-01-01T00:00:00+00:00"
OVERSIZE_BYTES = 5_000_000
_PDF_DATE = datetime(2026, 1, 1, tzinfo=UTC)


def synthetic_person_names() -> set[str]:
    """Every person name the generators may emit."""
    return set(person_pool())


def _instrument(
    rng: random.Random,
    doc_type: str,
    ordinal: int,
    parcel: str,
    address: str,
    book: int,
    page: int,
    refers_to_ref: str | None = None,
) -> dict:
    people, lenders = person_pool(), lender_pool()
    recorded = date(2023, 1, 1) + timedelta(days=rng.randint(0, 700))
    entry = {
        "ordinal": ordinal,
        "doc_type": doc_type,
        "parcel_key": parcel,
        "address_raw": f"{address}, Mattapoisett, MA",
        "recorded_date": recorded.isoformat(),
        "recorded_book": str(book),
        "recorded_page": str(page),
    }
    if doc_type == "deed":
        entry.update(grantor=rng.choice(people), grantee=rng.choice(people))
    elif doc_type == "mortgage":
        term = rng.choice([12, 18, 24, 360])
        entry.update(
            grantor=rng.choice(people),
            grantee=rng.choice(lenders),
            principal=f"{rng.randint(10, 150) * 10000}.00",
            term_months=term,
            maturity_date=(recorded + timedelta(days=int(term * 30.4375))).isoformat(),
        )
    else:
        entry.update(
            grantor=rng.choice(lenders), grantee=rng.choice(people), refers_to_ref=refers_to_ref
        )
    return entry


def _fmt_date(iso: str) -> str:
    return date.fromisoformat(iso).strftime("%B %d, %Y").replace(" 0", " ")


def _instrument_lines(i: dict) -> list[str]:
    title = {"deed": "QUITCLAIM DEED", "mortgage": "MORTGAGE", "discharge": "DISCHARGE OF MORTGAGE"}
    lines = [
        title[i["doc_type"]],
        f"Grantor: {i['grantor']}",
        f"Grantee: {i['grantee']}",
        f"Property: {i['address_raw']}",
        f"Parcel: {i['parcel_key']}",
        f"Recorded: {_fmt_date(i['recorded_date'])}  Book {i['recorded_book']} Page "
        f"{i['recorded_page']}",
    ]
    if i["doc_type"] == "mortgage":
        lines += [
            f"Principal: ${int(float(i['principal'])):,}.00",
            f"Term: {i['term_months']} months",
        ]
    if i["doc_type"] == "discharge":
        lines.append(f"Discharges mortgage recorded at {i['refers_to_ref']}")
    lines.append("SYNTHETIC TEST DOCUMENT - NOT A REAL RECORD")
    return lines


def _new_pdf() -> FPDF:
    pdf = FPDF()
    pdf.set_creation_date(_PDF_DATE)
    pdf.set_compression(False)
    pdf.set_title("synthetic fixture")
    return pdf


def _text_pdf(instruments: list[dict]) -> bytes:
    pdf = _new_pdf()
    pdf.set_font("Helvetica", size=12)
    for inst in instruments:
        pdf.add_page()
        for line in _instrument_lines(inst):
            pdf.cell(0, 8, line, new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())


def _image_pdf(png: bytes, width_mm: float = 190) -> bytes:
    pdf = _new_pdf()
    pdf.add_page()
    pdf.image(io.BytesIO(png), x=10, y=10, w=width_mm)
    return bytes(pdf.output())


def _scanned_pdf(instrument: dict) -> bytes:
    img = Image.new("L", (1200, 700), 255)
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=28)
    for n, line in enumerate(_instrument_lines(instrument)):
        draw.text((40, 30 + n * 55), line, fill=0, font=font)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return _image_pdf(buf.getvalue())


def _oversize_pdf(rng: random.Random) -> bytes:
    side = (2400, 2200)
    img = Image.frombytes("L", side, rng.randbytes(side[0] * side[1]))
    buf = io.BytesIO()
    img.save(buf, format="PNG", compress_level=0)
    data = _image_pdf(buf.getvalue())
    assert len(data) >= OVERSIZE_BYTES
    return data


def _census(address: str, kind: str) -> dict:
    base_req = {
        "endpoint": "locations/onelineaddress",
        "benchmark": "Public_AR_Current",
        "address": address,
    }
    result: dict = {"input": {"address": {"address": address}}, "addressMatches": []}
    if kind == "matched":
        result["addressMatches"] = [
            {
                "matchedAddress": address.upper(),
                "coordinates": {"x": -70.8161, "y": 41.6532},
                "tigerLine": {"tigerLineId": "0000000", "side": "L"},
                "addressComponents": {"city": "MATTAPOISETT", "state": "MA", "zip": "02739"},
            }
        ]
    elif kind == "low_match":
        result["addressMatches"] = [
            {
                "matchedAddress": "MAIN ST, MATTAPOISETT, MA, 02739",  # street-level only
                "coordinates": {"x": -70.8302, "y": 41.6601},
                "tigerLine": {"tigerLineId": "0000001", "side": "R"},
                "addressComponents": {"city": "MATTAPOISETT", "state": "MA", "zip": "02739"},
            }
        ]
    return {
        "_provenance": "Synthetic recording shaped like a Census geocoder response; "
        "not a captured live response.",
        "request": base_req,
        "expected_geocode_status": kind,
        "response": {"result": result},
    }


TOPOJSON = {
    "type": "Topology",
    "objects": {
        "mattapoisett": {
            "type": "GeometryCollection",
            "geometries": [
                {
                    "type": "Polygon",
                    "id": "mattapoisett",
                    "arcs": [[0]],
                    "properties": {"name": "Mattapoisett (placeholder outline)"},
                }
            ],
        }
    },
    "arcs": [
        [
            [-70.87, 41.685],
            [-70.82, 41.692],
            [-70.775, 41.685],
            [-70.758, 41.665],
            [-70.768, 41.640],
            [-70.800, 41.626],
            [-70.835, 41.630],
            [-70.862, 41.650],
            [-70.87, 41.685],
        ]
    ],
    "bbox": [-70.87, 41.626, -70.758, 41.692],
}
TOPOJSON_README = """# Mattapoisett TopoJSON (placeholder outline)

`mattapoisett.topo.json` is a **small hand-written, simplified polygon** drawn by eye around the
approximate extent of Mattapoisett, MA. It is a development placeholder so the map has a boundary
to draw offline.

- It is **NOT official data**: not derived from MassGIS, the Census TIGER/Line files or any
  surveyed source, and it is not accurate. Do not use it for analysis.
- Because it is original hand-written geometry it carries no third-party license; we place it in
  the public domain (CC0).
- Replace it with a real town boundary (for example the MassGIS municipal boundary, after
  checking its license terms) before any non-placeholder use.
"""


def _write_json(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n")


def generate_all(out_dir: Path | str, with_5k: bool = False) -> Path:
    """Generate every fixture under out_dir. Deterministic for the fixed seed."""
    out = Path(out_dir)
    rng = random.Random(FIXTURE_SEED)
    addresses = address_pool()

    def doc(doc_type: str, n: int, **kw) -> dict:
        return _instrument(
            rng, doc_type, 0, f"MATT-{10 + n}-{n}", rng.choice(addresses), 100 + n, 10 + n, **kw
        )

    singles = {
        "mortgage": doc("mortgage", 1),
        "deed": doc("deed", 2),
        "discharge": doc("discharge", 3, refers_to_ref="101/11"),
        "scanned_image": doc("mortgage", 4),
    }
    parcel, address = "MATT-50-5", rng.choice(addresses)
    multi = [
        _instrument(rng, "deed", 0, parcel, address, 200, 1),
        _instrument(rng, "mortgage", 1, parcel, address, 200, 2),
        _instrument(rng, "discharge", 2, parcel, address, 200, 3, refers_to_ref="200/2"),
    ]
    # Ground truth: the multi-instrument chain is ordered deed < mortgage < discharge.
    multi[0]["recorded_date"] = "2023-02-01"
    multi[1]["recorded_date"] = "2023-03-15"
    multi[2]["recorded_date"] = "2024-06-01"
    multi[1]["maturity_date"] = "2024-03-15"
    multi[1]["term_months"] = 12

    pdfs = out / "pdfs"
    pdfs.mkdir(parents=True, exist_ok=True)
    for stem in ("mortgage", "deed", "discharge"):
        (pdfs / f"{stem}.pdf").write_bytes(_text_pdf([singles[stem]]))
    (pdfs / "scanned_image.pdf").write_bytes(_scanned_pdf(singles["scanned_image"]))
    (pdfs / "multi_instrument.pdf").write_bytes(_text_pdf(multi))
    good = _text_pdf([singles["mortgage"]])
    (pdfs / "corrupt.pdf").write_bytes(good[: len(good) * 6 // 10] + b"\x00\xffNOT-A-PDF")
    (pdfs / "oversize.pdf").write_bytes(_oversize_pdf(rng))

    for stem, inst in singles.items():
        _write_json(out / "truth" / f"{stem}.json", {"doc": f"{stem}.pdf", "instruments": [inst]})
    _write_json(
        out / "truth" / "multi_instrument.json",
        {"doc": "multi_instrument.pdf", "instruments": multi},
    )

    addr = f"{rng.choice(addresses)}, Mattapoisett, MA"
    for kind in ("matched", "low_match", "no_match"):
        _write_json(out / "census" / f"{kind}.json", _census(addr, kind))

    build_snapshot(
        synthetic_rows(1, seed=FIXTURE_SEED),
        out / "snapshots" / "single",
        generated_at=GENERATED_AT,
    )
    if with_5k:
        build_snapshot(
            synthetic_rows(5000, seed=FIXTURE_SEED),
            out / "snapshots" / "synthetic_5k",
            generated_at=GENERATED_AT,
        )

    _write_json(out / "topojson" / f"{TOWN}.topo.json", TOPOJSON)
    (out / "topojson" / "README.md").write_text(TOPOJSON_README)
    return out
