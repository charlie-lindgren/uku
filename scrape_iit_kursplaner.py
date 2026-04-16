#!/usr/bin/env python3
"""
Scraper för ALLA IIT-kursplaner från du.se → Obsidian vault.

Upptäcker alla ämnen vid Institutionen för Informationsteknologi (IIT),
hämtar kurslistor per ämne via söksidans API, och skrapar varje kursplan
(svenska + engelska). Genererar Obsidian markdown-filer organiserade
per ämne med MOC-filer.

Användning:
    python3 scrape_iit_kursplaner.py                    # discovery + dry-run
    python3 scrape_iit_kursplaner.py --apply             # discovery + skriv
    python3 scrape_iit_kursplaner.py --subject DTA       # bara Datateknik
    python3 scrape_iit_kursplaner.py --list-subjects     # visa ämnen, avsluta
    python3 scrape_iit_kursplaner.py --list-courses      # visa alla kurser
    python3 scrape_iit_kursplaner.py GIK29B GDT34Z       # enskilda kurser
"""

import argparse
import hashlib
import math
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

VAULT_KURSPLANER = Path(__file__).resolve().parent / "vault-uku" / "02 Kursplaner"

IIT_URL = "https://www.du.se/sv/medarbetarwebb/organisation_styrning/organisation/institutioner/institutionen-for-information-och-teknik/"
SEARCH_API = "https://www.du.se/search/Search/Search"
SV_URL = "https://www.du.se/sv/utbildning/kurser/kursplan/?code={code}"
EN_URL = "https://www.du.se/en/study-at-du/kurser/syllabus/?code={code}"

REQUEST_DELAY = 1.0  # sekunder mellan anrop

# Sektioner vi vill ha i filen, i ordning.
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

SECTION_ORDER_SV = [
    "Lärandemål", "Innehåll", "Examinationsformer", "Arbetsformer",
    "Betyg", "Förkunskapskrav", "Övrigt", "Litteratur",
]

SECTION_ORDER_EN = [
    "Learning Outcomes", "Course Content", "Assessment", "Forms of Study",
    "Grades", "Prerequisites", "Other", "Reading List",
]

RESULTS_PER_PAGE = 20


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "UKU-kursplan-scraper/1.0 (intern revision HDa)"
})


def fetch_page(url: str, params: dict | None = None) -> BeautifulSoup | None:
    try:
        resp = SESSION.get(url, params=params, timeout=30)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")
    except requests.RequestException as e:
        print(f"  ⚠ Kunde inte hämta {url}: {e}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# Steg 1: Upptäck ämnen från IIT-sidan
# ---------------------------------------------------------------------------

def discover_subjects() -> list[dict]:
    """
    Skrapar IIT-sidan och returnerar lista med ämnen.
    Varje ämne: {id, name, code, esu, huvudomrade, type}
    type = 'subject' eller 'research'
    """
    soup = fetch_page(IIT_URL)
    if soup is None:
        print("Kunde inte hämta IIT-sidan!", file=sys.stderr)
        sys.exit(1)

    subjects = []

    # --- Grundutbildningsämnen ---
    subj_accordion = soup.find("ul", id="subjects-accordion")
    if subj_accordion:
        for li in subj_accordion.find_all("li", class_="panel", recursive=False):
            subj = _parse_subject_panel(li, "subject")
            if subj:
                subjects.append(subj)

    # --- Forskarutbildningsämnen ---
    research_accordion = soup.find("ul", id="postgraduatesubjects-accordion")
    if research_accordion:
        for li in research_accordion.find_all("li", class_="panel", recursive=False):
            subj = _parse_subject_panel(li, "research")
            if subj:
                subjects.append(subj)

    return subjects


def _parse_subject_panel(li: Tag, subject_type: str) -> dict | None:
    """Parsar ett ämnes-panel (li) och returnerar ämnesdikt."""
    button = li.find("button")
    if not button:
        return None

    name = button.get_text(strip=True)

    # Hitta ämnes-kod från collapse-ID (t.ex. collapse-DTA → DTA)
    panel_body = li.find("div", class_="panel-body")
    if not panel_body:
        return None
    collapse_id = panel_body.get("id", "")
    code = collapse_id.replace("collapse-", "") if collapse_id.startswith("collapse-") else ""

    # Hitta esu-ID från söklänken
    esu = ""
    search_link = panel_body.find("a", href=re.compile(r"esu="))
    if search_link:
        m = re.search(r"esu=(\d+)", search_link["href"])
        if m:
            esu = m.group(1)

    # Huvudområde/Område
    huvudomrade = ""
    dl = panel_body.find("dl")
    if dl:
        for dt in dl.find_all("dt"):
            key = dt.get_text(strip=True)
            dd = dt.find_next_sibling("dd")
            if dd and key in ("Huvudområde(n):", "Område:"):
                huvudomrade = dd.get_text(strip=True)

    return {
        "name": name,
        "code": code,
        "esu": esu,
        "huvudomrade": huvudomrade,
        "type": subject_type,
    }


# ---------------------------------------------------------------------------
# Steg 2: Hämta kurskoder per ämne via sök-API
# ---------------------------------------------------------------------------

def discover_courses_for_subject(esu: str) -> list[dict]:
    """
    Hämtar alla kurser för ett ämne (esu-ID) via söksidans API.
    Returnerar lista av {code, name}.
    """
    courses = []
    seen_codes = set()
    page = 1

    while True:
        params = {
            "search": "true", "q": "", "l": "sv", "sb": "Relevans",
            "ssv": "1", "f": "2", "cs": "4", "pi": str(page), "esu": esu, "et": "2",
        }
        soup = fetch_page(SEARCH_API, params=params)
        if soup is None:
            break

        # Extrahera totalt antal från "Resultat X - Y av Z träffar"
        total = 0
        for el in soup.find_all(string=re.compile(r"Resultat \d+ - \d+ av \d+ träffar")):
            m = re.search(r"av (\d+) träffar", el)
            if m:
                total = int(m.group(1))
                break

        # Extrahera kurskoder
        page_courses = []
        for a in soup.find_all("a", href=True):
            if "/kurser/kurs/?code=" in a["href"]:
                code = a["href"].split("code=")[1]
                name = a.get_text(strip=True)
                if code not in seen_codes:
                    seen_codes.add(code)
                    page_courses.append({"code": code, "name": name})

        courses.extend(page_courses)

        # Kontrollera om det finns fler sidor
        if total == 0 or len(courses) >= total or not page_courses:
            break

        page += 1
        time.sleep(REQUEST_DELAY)

    return courses


# ---------------------------------------------------------------------------
# Steg 3: Skrapa enskild kursplan
# ---------------------------------------------------------------------------

def html_to_markdown(element) -> str:
    if element is None:
        return ""
    parts = []
    _walk(element, parts)
    text = "".join(parts)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _walk(node, parts, list_depth=0):
    if isinstance(node, str):
        text = re.sub(r"[ \t]+", " ", str(node))
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


def extract_course_name(soup: BeautifulSoup) -> str:
    h1 = soup.find("h1")
    if h1:
        span = h1.find("span", property="name")
        if span:
            return span.get_text(strip=True)
        return h1.get_text(strip=True)
    return ""


def extract_metadata(soup: BeautifulSoup) -> dict:
    meta = {}
    dl = soup.find("dl", class_="dl-horizontal")
    if not dl:
        return meta
    for dt in dl.find_all("dt"):
        key = dt.get_text(strip=True)
        dd = dt.find_next_sibling("dd")
        if dd:
            meta[key] = dd.get_text(" ", strip=True)
    return meta


def extract_sections(soup: BeautifulSoup, section_map: dict) -> dict:
    article = soup.find("article", id="PageArticleArea")
    if not article:
        article = soup

    sections = {}
    for h2 in article.find_all("h2"):
        heading = h2.get_text(strip=True)
        target_key = None
        for key, aliases in section_map.items():
            if heading in aliases:
                target_key = key
                break
        if target_key is None:
            target_key = heading

        content_parts = []
        sibling = h2.find_next_sibling()
        while sibling and sibling.name != "h2":
            content_parts.append(sibling)
            sibling = sibling.find_next_sibling()

        md_parts = [html_to_markdown(p) for p in content_parts]
        md_text = "\n\n".join(p for p in md_parts if p)
        if md_text:
            sections[target_key] = md_text

    return sections


def scrape_course(code: str) -> dict | None:
    sv_soup = fetch_page(SV_URL.format(code=code))
    time.sleep(REQUEST_DELAY)
    en_soup = fetch_page(EN_URL.format(code=code))
    time.sleep(REQUEST_DELAY)

    if sv_soup is None:
        return None

    return {
        "code": code,
        "name_sv": extract_course_name(sv_soup),
        "name_en": extract_course_name(en_soup) if en_soup else "",
        "metadata": extract_metadata(sv_soup),
        "sections_sv": extract_sections(sv_soup, SECTION_MAP_SV),
        "sections_en": extract_sections(en_soup, SECTION_MAP_EN) if en_soup else {},
    }


# ---------------------------------------------------------------------------
# Filhantering
# ---------------------------------------------------------------------------

def content_hash(text: str) -> str:
    normalized = re.sub(r"\s+", " ", text.strip().lower())
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


def normalize_for_compare(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def build_course_markdown(scraped: dict, subject_name: str, subject_code: str) -> str:
    """Bygger en komplett kursplansfil från skrapade data."""
    code = scraped["code"]
    name_sv = scraped["name_sv"]
    name_en = scraped["name_en"]
    meta = scraped["metadata"]

    scraped_text_for_hash = str(scraped["sections_sv"]) + str(scraped["sections_en"])
    s_hash = content_hash(scraped_text_for_hash)

    # Frontmatter
    lines = [
        "---",
        f"kurskod: {code}",
        f"kursnamn: \"{name_sv}\"",
    ]
    if name_en and name_en != name_sv:
        lines.append(f"course_name: \"{name_en}\"")
    if "Högskolepoäng:" in meta:
        lines.append(f"hp: {meta['Högskolepoäng:']}")
    if "Nivå:" in meta:
        lines.append(f"niva: \"{meta['Nivå:']}\"")
    if "Huvudområde:" in meta:
        lines.append(f"huvudomrade: \"{meta['Huvudområde:']}\"")
    lines.append(f"amne: \"{subject_name}\"")
    lines.append(f"amne_kod: \"{subject_code}\"")
    lines.append(f"tags: [kursplan, {subject_code}]")
    lines.append(f"scrape_hash: {s_hash}")
    lines.append(f"up: \"[[{subject_name} MOC]]\"")
    lines.append("---")
    lines.append("")

    # Header
    lines.append(f"# {code}")
    lines.append("")
    lines.append(f"**Kursnamn:** {name_sv}")
    if name_en and name_en != name_sv:
        lines.append(f"**Course Name:** {name_en}")
    lines.append("")

    # Metadata-block
    meta_keys = ["Högskolepoäng:", "Nivå:", "Huvudområde:", "Ämnesgrupp:", "Utbildningsområde:"]
    for key in meta_keys:
        if key in meta:
            lines.append(f"- **{key}** {meta[key]}")
    if any(k in meta for k in meta_keys):
        lines.append("")

    # Svenska sektioner
    for section_name in SECTION_ORDER_SV:
        text = scraped["sections_sv"].get(section_name, "")
        if text:
            lines.append(f"## {section_name}")
            lines.append("")
            lines.append(text)
            lines.append("")

    # Engelska sektioner
    en_parts = []
    for section_name in SECTION_ORDER_EN:
        text = scraped["sections_en"].get(section_name, "")
        if text:
            en_parts.append(f"### {section_name}\n\n{text}")
    if en_parts:
        lines.append("## English Version")
        lines.append("")
        lines.append("\n\n".join(en_parts))
        lines.append("")

    return "\n".join(lines)


def update_existing_file(path: Path, scraped: dict, subject_name: str, subject_code: str) -> list[str]:
    """Uppdaterar en befintlig fil. Returnerar lista av ändringar."""
    text = path.read_text(encoding="utf-8")
    changes = []

    # Parse existing
    result = {"frontmatter": "", "header_block": "", "sections": {}, "section_order": []}

    fm_match = re.match(r"^---\n(.*?\n)---\n", text, re.DOTALL)
    if fm_match:
        result["frontmatter"] = fm_match.group(0)
        rest = text[fm_match.end():]
    else:
        rest = text

    first_section = re.search(r"^## ", rest, re.MULTILINE)
    if first_section:
        result["header_block"] = rest[:first_section.start()].rstrip("\n")
        sections_text = rest[first_section.start():]
    else:
        result["header_block"] = rest.rstrip("\n")
        sections_text = ""

    section_re = re.compile(r"^## (.+)$", re.MULTILINE)
    matches = list(section_re.finditer(sections_text))
    for i, m in enumerate(matches):
        name = m.group(1).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(sections_text)
        body = sections_text[start:end].strip()
        result["sections"][name] = body
        result["section_order"].append(name)

    # Check for content changes
    for section_name in SECTION_ORDER_SV:
        scraped_sv = scraped["sections_sv"].get(section_name, "")
        if not scraped_sv:
            continue
        if section_name in result["sections"]:
            if normalize_for_compare(result["sections"][section_name]) != normalize_for_compare(scraped_sv):
                changes.append(f"  ↻ {section_name}")
        else:
            changes.append(f"  + {section_name}")

    en_parts = []
    for section_name in SECTION_ORDER_EN:
        scraped_en = scraped["sections_en"].get(section_name, "")
        if scraped_en:
            en_parts.append(f"### {section_name}\n\n{scraped_en}")
    if en_parts:
        new_en = "\n\n".join(en_parts)
        existing_en = result["sections"].get("English Version", "")
        if not existing_en:
            changes.append("  + English Version")
        elif normalize_for_compare(existing_en) != normalize_for_compare(new_en):
            changes.append("  ↻ English Version")

    # Update scrape_hash in frontmatter
    scraped_text_for_hash = str(scraped["sections_sv"]) + str(scraped["sections_en"])
    new_hash = content_hash(scraped_text_for_hash)
    fm = result["frontmatter"]
    if "scrape_hash:" in fm:
        old_hash = re.search(r"scrape_hash: (\S+)", fm)
        if old_hash and old_hash.group(1) == new_hash and not changes:
            return []  # Ingen förändring

    return changes


def write_course_file(
    code: str, scraped: dict, subject_name: str, subject_code: str,
    subject_dir: Path, apply: bool, quiet: bool
) -> int:
    """Skriver/uppdaterar en kursplansfil. Returnerar antal ändringar."""
    file_path = subject_dir / f"{code}.md"

    if file_path.exists():
        changes = update_existing_file(file_path, scraped, subject_name, subject_code)
        if not changes:
            if not quiet:
                print("inga ändringar")
            return 0
        if not quiet:
            print(f"{len(changes)} ändring(ar)")
            for c in changes:
                print(c)
        if apply:
            new_text = build_course_markdown(scraped, subject_name, subject_code)
            file_path.write_text(new_text, encoding="utf-8")
            if not quiet:
                print(f"  ✓ Uppdaterad: {file_path.relative_to(VAULT_KURSPLANER.parent)}")
        return len(changes)
    else:
        # Ny fil
        if not quiet:
            print("ny kurs")
        if apply:
            new_text = build_course_markdown(scraped, subject_name, subject_code)
            file_path.write_text(new_text, encoding="utf-8")
            if not quiet:
                print(f"  ✓ Skapad: {file_path.relative_to(VAULT_KURSPLANER.parent)}")
        return 1


# ---------------------------------------------------------------------------
# MOC-generering
# ---------------------------------------------------------------------------

def build_subject_moc(subject: dict, courses: list[dict]) -> str:
    """Bygger en ämnes-MOC fil."""
    name = subject["name"]
    code = subject["code"]
    huvudomrade = subject.get("huvudomrade", "")
    stype = subject["type"]
    type_label = "Forskarutbildningsämne" if stype == "research" else "Ämne"

    lines = [
        "---",
        f"aliases: [{name}]",
        f"tags: [MOC, amne, {code}]",
        f"up: \"[[Kursplaner MOC]]\"",
        "---",
        "",
        f"# {name} MOC",
        "",
        f"> {type_label} vid IIT, Högskolan Dalarna.",
    ]
    if huvudomrade:
        lines.append(f"> Huvudområde: {huvudomrade}")
    lines.append("")

    lines.append(f"## Kurser ({len(courses)} st)")
    lines.append("")

    if courses:
        for c in sorted(courses, key=lambda x: x["code"]):
            lines.append(f"- [[{c['code']}]] — {c['name']}")
    else:
        lines.append("_Inga kurser hittade._")
    lines.append("")

    return "\n".join(lines)


def build_main_moc(subjects: list[dict], course_counts: dict[str, int]) -> str:
    """Bygger huvud-MOC som listar alla ämnen."""
    lines = [
        "---",
        "aliases: [Kursplaner, Kurser]",
        "tags: [kursplaner, MOC]",
        "up: \"[[UKU Dashboard]]\"",
        "---",
        "",
        "# Kursplaner MOC",
        "",
        "> Samtliga kursplaner vid IIT organiserade per ämne.",
        "",
        "## Ämnen",
        "",
    ]

    regular = [s for s in subjects if s["type"] == "subject"]
    research = [s for s in subjects if s["type"] == "research"]

    for s in sorted(regular, key=lambda x: x["name"]):
        count = course_counts.get(s["code"], 0)
        lines.append(f"- [[{s['name']} MOC|{s['name']}]] ({count} kurser)")
    lines.append("")

    if research:
        lines.append("## Forskarutbildningsämnen")
        lines.append("")
        for s in sorted(research, key=lambda x: x["name"]):
            count = course_counts.get(s["code"], 0)
            lines.append(f"- [[{s['name']} MOC|{s['name']}]] ({count} kurser)")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Huvudprogram
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Skrapar alla IIT-kursplaner från du.se till Obsidian-vaulten."
    )
    parser.add_argument(
        "courses", nargs="*",
        help="Specifika kurskod(er). Utelämna för alla."
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Skriv ändringar till disk (annars dry-run)."
    )
    parser.add_argument(
        "--quiet", "-q", action="store_true",
        help="Minimal utskrift."
    )
    parser.add_argument(
        "--subject", "-s", action="append",
        help="Begränsa till specifikt ämne (kod, t.ex. DTA). Kan anges flera gånger."
    )
    parser.add_argument(
        "--list-subjects", action="store_true",
        help="Lista alla ämnen och avsluta."
    )
    parser.add_argument(
        "--list-courses", action="store_true",
        help="Lista alla kurser per ämne och avsluta."
    )
    args = parser.parse_args()

    # --- Steg 1: Upptäck ämnen ---
    print("Hämtar ämnen från IIT-sidan...")
    subjects = discover_subjects()
    print(f"  Hittade {len(subjects)} ämnen")

    if args.list_subjects:
        print()
        for s in subjects:
            label = "F" if s["type"] == "research" else " "
            esu = s["esu"] or "?"
            print(f"  [{label}] {s['code']:12s} {s['name']:40s} esu={esu}")
        return

    # Filtrera ämnen om --subject angivits
    if args.subject:
        codes = set(c.upper() for c in args.subject)
        subjects = [s for s in subjects if s["code"].upper() in codes]
        if not subjects:
            print(f"Inga ämnen matchade: {args.subject}", file=sys.stderr)
            sys.exit(1)

    # --- Steg 2: Hämta kurser per ämne ---
    print("\nHämtar kurslistor per ämne...")
    subject_courses: dict[str, list[dict]] = {}

    for s in subjects:
        if not s["esu"]:
            print(f"  {s['name']}: ingen esu-ID, hoppar över")
            continue
        print(f"  {s['name']}...", end=" ", flush=True)
        courses = discover_courses_for_subject(s["esu"])
        subject_courses[s["code"]] = courses
        print(f"{len(courses)} kurser")
        time.sleep(REQUEST_DELAY)

    total_courses = sum(len(v) for v in subject_courses.values())
    print(f"\n  Totalt: {total_courses} kurser")

    if args.list_courses:
        print()
        for s in subjects:
            courses = subject_courses.get(s["code"], [])
            print(f"\n  {s['name']} ({s['code']}):")
            for c in sorted(courses, key=lambda x: x["code"]):
                print(f"    {c['code']:10s} {c['name']}")
        return

    # Om specifika kurskoder angetts, filtrera
    if args.courses:
        target_codes = set(c.upper() for c in args.courses)
        for code in subject_courses:
            subject_courses[code] = [
                c for c in subject_courses[code] if c["code"].upper() in target_codes
            ]
        total_courses = sum(len(v) for v in subject_courses.values())
        not_found = target_codes - {
            c["code"].upper()
            for courses in subject_courses.values()
            for c in courses
        }
        if not_found:
            print(f"\n  ⚠ Kurskoder ej funna i något ämne: {', '.join(sorted(not_found))}")

    # --- Steg 3: Skrapa och skriv ---
    mode = "SKRIVER" if args.apply else "DRY-RUN"
    print(f"\n╔══════════════════════════════════════════════╗")
    print(f"║  IIT Kursplan-scraper — {mode:8s}            ║")
    print(f"║  {total_courses:3d} kurs(er) att bearbeta                ║")
    print(f"╚══════════════════════════════════════════════╝\n")

    total_changes = 0
    total_errors = 0
    course_num = 0

    for s in subjects:
        courses = subject_courses.get(s["code"], [])
        if not courses:
            continue

        subject_dir = VAULT_KURSPLANER / s["code"]
        if args.apply:
            subject_dir.mkdir(parents=True, exist_ok=True)

        if not args.quiet:
            print(f"\n── {s['name']} ({s['code']}) ──")

        for c in sorted(courses, key=lambda x: x["code"]):
            course_num += 1
            code = c["code"]
            if not args.quiet:
                print(f"  [{course_num}/{total_courses}] {code} ({c['name']})...", end=" ", flush=True)

            try:
                scraped = scrape_course(code)
                if scraped is None:
                    if not args.quiet:
                        print("misslyckades")
                    total_errors += 1
                    continue

                n = write_course_file(code, scraped, s["name"], s["code"], subject_dir, args.apply, args.quiet)
                total_changes += n
            except Exception as e:
                total_errors += 1
                print(f"\n  ✗ Fel vid {code}: {e}", file=sys.stderr)

    # --- Steg 4: Uppdatera MOC-filer ---
    if args.apply:
        print("\nUppdaterar MOC-filer...")

        course_counts = {code: len(courses) for code, courses in subject_courses.items()}

        # Ämnes-MOC:ar
        for s in subjects:
            courses = subject_courses.get(s["code"], [])
            if not courses and not s["esu"]:
                continue
            subject_dir = VAULT_KURSPLANER / s["code"]
            subject_dir.mkdir(parents=True, exist_ok=True)
            moc_path = VAULT_KURSPLANER / f"{s['name']} MOC.md"
            moc_text = build_subject_moc(s, courses)
            moc_path.write_text(moc_text, encoding="utf-8")
            if not args.quiet:
                print(f"  ✓ {moc_path.name}")

        # Huvud-MOC
        moc_path = VAULT_KURSPLANER / "Kursplaner MOC.md"
        moc_text = build_main_moc(subjects, course_counts)
        moc_path.write_text(moc_text, encoding="utf-8")
        if not args.quiet:
            print(f"  ✓ {moc_path.name}")

    # Summering
    print(f"\nKlart! {course_num} kurser bearbetade, "
          f"{total_changes} ändring(ar), {total_errors} fel.")

    if not args.apply and total_changes > 0:
        print("Kör igen med --apply för att spara ändringarna.")


if __name__ == "__main__":
    main()
