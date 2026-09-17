"""Find the latest published Berkeley offerings and their associated sections."""
import re
from urllib.parse import urlencode, urljoin, urlparse

from bs4 import BeautifulSoup

from courses import discover_course
from lecture_context import HOST, SECTION
from monitor import fetch_page


def section_link(href):
    parsed = urlparse(urljoin(HOST, href))
    match = SECTION.fullmatch(parsed.path)
    if parsed.scheme == "https" and parsed.netloc == "classes.berkeley.edu" and match:
        return HOST + parsed.path.rstrip("/"), match
    return None, None


def search_courses(query, term_id="", page=0):
    if not isinstance(query, str) or not 2 <= len(query.strip()) <= 160:
        raise ValueError("Enter a class title or course code (2–160 characters).")
    if not isinstance(term_id, str) or (term_id and not term_id.isdigit()) or type(page) is not int or not 0 <= page <= 1000:
        raise ValueError("Invalid search page.")
    query = " ".join(query.replace('"', ' ').split())
    query = re.sub(r"^([A-Za-z]+)(\d)", r"\1 \2", query)
    if len(query) < 2:
        raise ValueError("Enter a class title or course code.")
    params = {"search": f'"{query}"'}
    if not term_id:
        soup = BeautifulSoup(fetch_page(HOST + "/search/class?" + urlencode(params)), "html.parser")
        terms = []
        for link in soup.select("#block-term a[data-drupal-facet-item-value]"):
            label = link.select_one(".facet-item__value")
            match = re.fullmatch(r"(Spring|Summer(?: Sessions)?|Fall|Winter) (\d{4})", label.get_text(strip=True) if label else "")
            value = link["data-drupal-facet-item-value"]
            if match and value.isdigit():
                season = match[1].split()[0]
                terms.append((int(match[2]), {"Winter":0, "Spring":1, "Summer":2, "Fall":3}[season], value))
        if not terms:
            raise ValueError("No matching published classes found. Try a course code or a more specific title.")
        term_id = max(terms)[2]
    params.update({"f[0]": f"term:{term_id}", "page": page})
    soup = BeautifulSoup(fetch_page(HOST + "/search/class?" + urlencode(params)), "html.parser")
    results = {}
    for row in soup.select(".views-row"):
        link = row.select_one(".st--section-name-wraper a[href]")
        url, match = section_link(link["href"]) if link else (None, None)
        if not match:
            continue
        def text(selector):
            node = row.select_one(selector)
            return " ".join(node.get_text(" ", strip=True).split()) if node else ""
        results[url] = {"url": url, "label": link.get_text(" ", strip=True),
                        "title": text(".st--title"), "term": text(".st--term-year"),
                        "details": " · ".join(filter(None, [text(".st--instructors"), text(".st--meetings")]))}
    return {"courses": list(results.values()), "term_id": term_id, "page": page,
            "more": soup.select_one(".pager__item--next a") is not None}


def course_sections(url):
    url, match = section_link(url) if isinstance(url, str) else (None, None)
    if not match:
        raise ValueError("Choose a Berkeley class from the search results.")
    profile = discover_course(url)
    sections = [{"url": url, "label": profile["label"], "details": profile["meeting"], "parent": ""}]
    soup = BeautifulSoup(fetch_page(url), "html.parser")
    node = soup.select_one('[data-element="associated_sections"][data-section-id]')
    if node is not None and str(node["data-section-id"]).isdigit():
        associated = BeautifulSoup(fetch_page(f"{HOST}/sections/associated/{node['data-section-id']}"), "html.parser")
        seen = {url}
        for block in associated.select(".detail-class-associated-sections-flex"):
            link = block.select_one("h4 a[href]")
            target, other = section_link(link["href"]) if link else (None, None)
            if not other or other[1].lower() != match[1].lower() or target in seen:
                continue
            seen.add(target)
            sections.append({"url": target, "label": f"{link.get_text(' ', strip=True)} {other[2].upper()}",
                             "details": " · ".join(label.parent.get_text(" ", strip=True) for label in block.select(".detail-label") if label.get_text(strip=True) in {"Days:", "Time:", "Place:"}),
                             "parent": profile["section_id"] if profile["component"] == "LEC" and other[2].lower() == "dis" else ""})
    return {"sections": sections, "term": profile["term"]}
