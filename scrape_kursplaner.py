#!/usr/bin/env python3
"""
Scraper för kursplaner från du.se → Obsidian vault.

Hämtar kursplaner (svenska + engelska) från Högskolan Dalarnas webbplats
och uppdaterar markdown-filer i vaultens 07 Kursplaner-katalog.

Användning:
    python3 scrape_kursplaner.py                  # alla kurser, dry-run
    python3 scrape_kursplaner.py --apply           # alla kurser, skriv ändringar
    python3 scrape_kursplaner.py GIK29B            # en kurs, dry-run
    python3 scrape_kursplaner.py GIK29B --apply    # en kurs, skriv ändringar
    python3 scrape_kursplaner.py --apply --quiet    # minimal utskrift
"""

import argparse
import hashlib
import os
import re
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup, Tag

# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------

VAULT_KURSPLANER = Path(__file__).resolve().parent / "vault-comp-26" / "07 Kursplaner"
SV_URL = "https://www.du.se/sv/utbildning/kurser/kursplan/?code={code}"
EN_URL = "https://www.du.se/en/study-at-du/kurser/syllabus/?code={code}"
REQUEST_DELAY = 1.0  # sekunder mellan anrop, var vänlig mot servern

# Sektioner vi vill ha i filen, i ordning.
# Nyckel = rubrik i markdown-filen; Värden = möjliga h2-rubriker på webben
SECTION_MAP_SV = {
    "Lärandemål": ["Lärandemål", "Mål"],
    "Innehåll": ["Innehåll"],
    "Examinationsformer": ["Examinationsformer"],
    "Arbetsformer": ["Arbetsformer"],
    "Betyg": ["Betyg"],
    "Förkunskapskrav": ["Förkunskapskrav"],
    "Övrigt": ["Övrigt"],
    "Litteratur": ["Litteratur"],
}

SECTION_MAP_EN = {
    "Learning Outcomes": ["Learning Outcomes", "Objectives"],
    "Course Content": ["Course Content"],
    "Assessment": ["Assessment"],
    "Forms of Study": ["Forms of Study"],
    "Grades": ["Grades"],
    "Prerequisites": ["Prerequisites"],
    "Other": ["Other"],
    "Reading List": ["Reading List"],
}

# Ordning för sektioner i den slutliga filen
SECTION_ORDER_SV = [
    "Lärandemål",
    "Innehåll",
    "Examinationsformer",
    "Arbetsformer",
    "Betyg",
    "Förkunskapskrav",
    "Övrigt",
    "Litteratur",
]

SECTION_ORDER_EN = [
    "Learning Outcomes",
    "Course Content",
    "Assessment",
    "Forms of Study",
    "Grades",
    "Prerequisites",
    "Other",
    "Reading List",
]


# ---------------------------------------------------------------------------
# HTML → Markdown-konvertering
# ---------------------------------------------------------------------------


def html_to_markdown(element) -> str:
    """Konverterar ett HTML-element till enkel markdown."""
    if element is None:
        return ""
    parts = []
    _walk(element, parts)
    text = "".join(parts)
    # Rensa upp
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()
    return text


def _walk(node, parts, list_depth=0):
    """Rekursiv genomgång av DOM-noder."""
    if isinstance(node, str):
        # NavigableString
        text = str(node)
        # Kollapsa whitespace men behåll enstaka mellanslag
        text = re.sub(r"[ \t]+", " ", text)
        parts.append(text)
        return

    if not isinstance(node, Tag):
        return

    tag = node.name

    if tag in ("ul", "ol"):
        parts.append("\n")
        for child in node.children:
            _walk(child, parts, list_depth + 1)
        parts.append("\n")
    elif tag == "li":
        indent = "  " * list_depth
        parts.append(f"\n{indent}- ")
        for child in node.children:
            _walk(child, parts, list_depth)
    elif tag == "br":
        parts.append("  \n")
    elif tag == "p":
        parts.append("\n\n")
        for child in node.children:
            _walk(child, parts, list_depth)
        parts.append("\n")
    elif tag in ("strong", "b"):
        parts.append("**")
        for child in node.children:
            _walk(child, parts, list_depth)
        parts.append("**")
    elif tag in ("em", "i"):
        parts.append("_")
        for child in node.children:
            _walk(child, parts, list_depth)
        parts.append("_")
    elif tag == "sup":
        # Ignorera fotnoter (t.ex. huvudområde-superscripts)
        pass
    elif tag in ("span", "div", "a"):
        for child in node.children:
            _walk(child, parts, list_depth)
    elif tag == "h3":
        parts.append("\n\n### ")
        for child in node.children:
            _walk(child, parts, list_depth)
        parts.append("\n\n")
    else:
        for child in node.children:
            _walk(child, parts, list_depth)


# ---------------------------------------------------------------------------
# Webbskrapning
# ---------------------------------------------------------------------------


def fetch_page(url: str) -> BeautifulSoup | None:
    """Hämtar en sida och returnerar BeautifulSoup, eller None vid fel."""
    try:
        resp = requests.get(url, timeout=30, headers={
            "User-Agent": "COMP26-kursplan-scraper/1.0 (intern revision HDa)"
        })
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")
    except requests.RequestException as e:
        print(f"  ⚠ Kunde inte hämta {url}: {e}", file=sys.stderr)
        return None


def extract_course_name(soup: BeautifulSoup) -> str:
    """Hämtar kursnamnet från sidans <h1>."""
    h1 = soup.find("h1")
    if h1:
        span = h1.find("span", property="name")
        if span:
            return span.get_text(strip=True)
        return h1.get_text(strip=True)
    return ""


def extract_metadata(soup: BeautifulSoup) -> dict:
    """Hämtar metadata från dl-listan i sidans header."""
    meta = {}
    dl = soup.find("dl", class_="dl-horizontal")
    if not dl:
        return meta
    dts = dl.find_all("dt")
    for dt in dts:
        key = dt.get_text(strip=True)
        dd = dt.find_next_sibling("dd")
        if dd:
            val = dd.get_text(" ", strip=True)
            meta[key] = val
    return meta


def extract_sections(soup: BeautifulSoup, section_map: dict) -> dict:
    """Extraherar sektioner (h2 → text) från kursplanssidan."""
    article = soup.find("article", id="PageArticleArea")
    if not article:
        article = soup  # fallback

    sections = {}
    h2s = article.find_all("h2")

    for h2 in h2s:
        heading = h2.get_text(strip=True)
        # Matcha mot kända sektionsnamn
        target_key = None
        for key, aliases in section_map.items():
            if heading in aliases:
                target_key = key
                break

        if target_key is None:
            # Okänd sektion — ta med den ändå med originalnamn
            target_key = heading

        # Samla alla sibling-element tills nästa h2
        content_parts = []
        sibling = h2.find_next_sibling()
        while sibling and sibling.name != "h2":
            content_parts.append(sibling)
            sibling = sibling.find_next_sibling()

        # Konvertera till markdown
        md_parts = []
        for part in content_parts:
            md_parts.append(html_to_markdown(part))
        md_text = "\n\n".join(p for p in md_parts if p)

        if md_text:
            sections[target_key] = md_text

    return sections


def scrape_course(code: str) -> dict | None:
    """Skrapar en kurs och returnerar all data."""
    sv_url = SV_URL.format(code=code)
    en_url = EN_URL.format(code=code)

    sv_soup = fetch_page(sv_url)
    time.sleep(REQUEST_DELAY)
    en_soup = fetch_page(en_url)
    time.sleep(REQUEST_DELAY)

    if sv_soup is None:
        return None

    data = {
        "code": code,
        "name_sv": extract_course_name(sv_soup),
        "name_en": extract_course_name(en_soup) if en_soup else "",
        "metadata": extract_metadata(sv_soup),
        "sections_sv": extract_sections(sv_soup, SECTION_MAP_SV),
        "sections_en": extract_sections(en_soup, SECTION_MAP_EN) if en_soup else {},
    }

    return data


# ---------------------------------------------------------------------------
# Filhantering — läsa/skriva vault-filer
# ---------------------------------------------------------------------------


def find_course_file(code: str) -> Path | None:
    """Hittar .md-filen för en kurskod."""
    for md in VAULT_KURSPLANER.rglob(f"{code}.md"):
        return md
    return None


def parse_existing_file(path: Path) -> dict:
    """Läser en befintlig kursplansfil och returnerar strukturerad data."""
    text = path.read_text(encoding="utf-8")

    result = {
        "raw": text,
        "frontmatter": "",
        "header_block": "",   # allt mellan --- och första ##
        "sections": {},       # rubrik → text
        "section_order": [],  # ordning som sektionerna förekommer
    }

    # Frontmatter
    fm_match = re.match(r"^---\n(.*?\n)---\n", text, re.DOTALL)
    if fm_match:
        result["frontmatter"] = fm_match.group(0)
        rest = text[fm_match.end():]
    else:
        rest = text

    # Header-block (allt före första ## )
    first_section = re.search(r"^## ", rest, re.MULTILINE)
    if first_section:
        result["header_block"] = rest[:first_section.start()].rstrip("\n")
        sections_text = rest[first_section.start():]
    else:
        result["header_block"] = rest.rstrip("\n")
        sections_text = ""

    # Sektioner
    section_re = re.compile(r"^## (.+)$", re.MULTILINE)
    matches = list(section_re.finditer(sections_text))
    for i, m in enumerate(matches):
        name = m.group(1).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(sections_text)
        body = sections_text[start:end].strip()
        result["sections"][name] = body
        result["section_order"].append(name)

    return result


def content_hash(text: str) -> str:
    """Beräknar hash av normaliserad text för jämförelse."""
    normalized = re.sub(r"\s+", " ", text.strip().lower())
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


def normalize_for_compare(text: str) -> str:
    """Normaliserar text för jämförelse (ignorerar whitespace-skillnader)."""
    return re.sub(r"\s+", " ", text.strip().lower())


def build_updated_file(existing: dict, scraped: dict) -> tuple[str, list[str]]:
    """
    Bygger en uppdaterad filtext baserat på befintlig + skrapade data.
    Returnerar (ny_text, lista_av_ändringar).
    """
    changes = []

    # --- Header-block: berika med kursnamn om det saknas ---
    header = existing["header_block"]

    # Lägg till svenskt kursnamn efter # KURSKOD om det saknas
    name_sv = scraped.get("name_sv", "")
    name_en = scraped.get("name_en", "")

    if name_sv and f"**Kursnamn:** {name_sv}" not in header:
        # Infoga kursnamn direkt efter # -raden
        h1_match = re.search(r"^(# \S+)", header, re.MULTILINE)
        if h1_match:
            insert_pos = h1_match.end()
            name_line = f"\n\n**Kursnamn:** {name_sv}"
            if name_en and name_en != name_sv:
                name_line += f"  \n**Course Name:** {name_en}"
            header = header[:insert_pos] + name_line + header[insert_pos:]
            changes.append(f"  + Kursnamn: {name_sv}")

    # --- Sektioner ---
    # Samla alla sektioner — behåll befintliga, lägg till/uppdatera från scrape
    final_sections = {}
    section_order = list(existing["section_order"])

    # Befintliga sektioner
    for name in section_order:
        final_sections[name] = existing["sections"][name]

    # Gå igenom skrapade sektioner
    for section_name in SECTION_ORDER_SV:
        scraped_sv = scraped["sections_sv"].get(section_name, "")
        if not scraped_sv:
            continue

        if section_name in final_sections:
            # Jämför
            old_norm = normalize_for_compare(final_sections[section_name])
            new_norm = normalize_for_compare(scraped_sv)
            if old_norm != new_norm:
                final_sections[section_name] = scraped_sv
                changes.append(f"  ↻ {section_name} (uppdaterad)")
        else:
            # Ny sektion
            final_sections[section_name] = scraped_sv
            if section_name not in section_order:
                section_order.append(section_name)
            changes.append(f"  + {section_name} (ny)")

    # Engelska sektioner — lägg till som undersektioner
    en_section_name = "English Version"
    existing_en = final_sections.get(en_section_name, "")
    en_parts = []
    for section_name in SECTION_ORDER_EN:
        scraped_en = scraped["sections_en"].get(section_name, "")
        if scraped_en:
            en_parts.append(f"### {section_name}\n\n{scraped_en}")

    if en_parts:
        new_en = "\n\n".join(en_parts)
        if en_section_name not in final_sections:
            changes.append(f"  + {en_section_name} (ny)")
            section_order.append(en_section_name)
        elif normalize_for_compare(existing_en) != normalize_for_compare(new_en):
            changes.append(f"  ↻ {en_section_name} (uppdaterad)")
        final_sections[en_section_name] = new_en

    # Sortera sektioner: respektera önskad ordning, sedan resten
    desired_order = SECTION_ORDER_SV + [en_section_name]
    ordered_sections = []
    for s in desired_order:
        if s in final_sections:
            ordered_sections.append(s)
    for s in section_order:
        if s not in ordered_sections and s in final_sections:
            ordered_sections.append(s)

    # --- Sätt ihop filen ---
    # Uppdatera frontmatter med scrape-hash
    fm = existing["frontmatter"]
    scraped_text_for_hash = str(scraped["sections_sv"]) + str(scraped["sections_en"])
    new_hash = content_hash(scraped_text_for_hash)

    if "scrape_hash:" in fm:
        fm = re.sub(r"scrape_hash: .*", f"scrape_hash: {new_hash}", fm)
    else:
        # Lägg till före sista ---
        fm = fm.rstrip().rstrip("-").rstrip() + f"\nscrape_hash: {new_hash}\n---\n"

    # Undvik dubbla blankrader
    header_stripped = header.lstrip("\n")
    parts = [fm.rstrip("\n"), "", header_stripped]
    for section_name in ordered_sections:
        body = final_sections[section_name]
        parts.append("")
        parts.append(f"## {section_name}")
        parts.append("")
        parts.append(body)

    result = "\n".join(parts) + "\n"
    return result, changes


# ---------------------------------------------------------------------------
# Huvudprogram
# ---------------------------------------------------------------------------


def process_course(code: str, apply: bool, quiet: bool) -> tuple[bool, int]:
    """
    Bearbetar en kurs. Returnerar (hittad, antal_ändringar).
    """
    md_path = find_course_file(code)
    if md_path is None:
        if not quiet:
            print(f"  ⚠ Ingen .md-fil hittad för {code}, hoppar över")
        return False, 0

    if not quiet:
        print(f"  Skrapar {code}...", end=" ", flush=True)

    scraped = scrape_course(code)
    if scraped is None:
        if not quiet:
            print("misslyckades")
        return True, 0

    existing = parse_existing_file(md_path)
    new_text, changes = build_updated_file(existing, scraped)

    if not changes:
        if not quiet:
            print("inga ändringar")
        return True, 0

    if not quiet:
        print(f"{len(changes)} ändring(ar)")
        for c in changes:
            print(c)

    if apply:
        md_path.write_text(new_text, encoding="utf-8")
        if not quiet:
            print(f"  ✓ Sparad: {md_path.relative_to(VAULT_KURSPLANER.parent.parent)}")
    else:
        if not quiet:
            print(f"  (dry-run — kör med --apply för att spara)")

    return True, len(changes)


def get_all_course_codes() -> list[str]:
    """Returnerar alla kurskoder i vaulten."""
    codes = []
    for md in VAULT_KURSPLANER.rglob("*.md"):
        codes.append(md.stem)
    return sorted(codes)


def main():
    parser = argparse.ArgumentParser(
        description="Skrapar kursplaner från du.se och uppdaterar Obsidian-vaulten."
    )
    parser.add_argument(
        "courses", nargs="*",
        help="Kurskod(er) att bearbeta. Utelämna för alla."
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Skriv ändringar till disk (annars dry-run)."
    )
    parser.add_argument(
        "--quiet", "-q", action="store_true",
        help="Minimal utskrift."
    )
    args = parser.parse_args()

    codes = args.courses if args.courses else get_all_course_codes()

    mode = "SKRIVER" if args.apply else "DRY-RUN"
    print(f"╔══════════════════════════════════════════════╗")
    print(f"║  Kursplan-scraper — {mode:8s}               ║")
    print(f"║  {len(codes)} kurs(er) att bearbeta                 ║")
    print(f"╚══════════════════════════════════════════════╝")
    print()

    total_changes = 0
    total_found = 0
    total_errors = 0

    for i, code in enumerate(codes, 1):
        if not args.quiet:
            print(f"[{i}/{len(codes)}] {code}")
        try:
            found, n_changes = process_course(code, args.apply, args.quiet)
            if found:
                total_found += 1
            total_changes += n_changes
        except Exception as e:
            total_errors += 1
            print(f"  ✗ Fel: {e}", file=sys.stderr)

    print()
    print(f"Klart! {total_found} kurser bearbetade, "
          f"{total_changes} ändring(ar), {total_errors} fel.")

    if not args.apply and total_changes > 0:
        print("Kör igen med --apply för att spara ändringarna.")


if __name__ == "__main__":
    main()
