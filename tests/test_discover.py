"""NASA's picture of the day from its own pages (core/discover.py, 2026-11-03): APOD moved to science.nasa.gov, and the
old api.nasa.gov feed gave errors or "NASA Science" with no picture, so every NASA card was a picture card without its
picture. The pages here are trimmed copies of the real ones."""
import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import discover

ARCHIVE = """<ul>
<li><a href="https://science.nasa.gov/image-article/apod-2026-november-3-comet-panstarrs-and-rainbow-meteor/">Nov 3</a></li>
<li><a href="https://science.nasa.gov/image-article/apod-2026-november-2-a-long-cloud-on-mars/">Nov 2</a></li>
<li><a href="https://science.nasa.gov/image-article/apod-29-october-2026-the-horns-of-venus/">Oct 29</a></li>
</ul>"""
ENTRY = """<html><head>
<meta property="og:title" content="APOD: 2026 November 3 - Comet PanSTARRs and Rainbow Meteor - NASA Science" />
<meta property="og:image" content="https://assets.science.nasa.gov/apod/2026/november/CometMeteor.jpg/r.1280.jpeg" />
</head><body>
<h1 class="display-48 margin-top-0">Comet PanSTARRs and Rainbow Meteor</h1>
<p class="p-md margin-0 media-detail-hero__description">
<strong>Explanation: </strong>Did this meteor come from that comet? Probably not. Although many
<a href="https://science.nasa.gov/meteors/">meteors</a> do originate from comets&#8230;</p>
</body></html>"""


def fetch_from(pages):
    def fetch(url):
        return pages[url].encode("utf-8"), "text/html"
    return fetch


def test_todays_picture_comes_with_its_title_text_and_picture():
    entry = "https://science.nasa.gov/image-article/apod-2026-november-3-comet-panstarrs-and-rainbow-meteor/"
    cards = discover.apod(fetch_from({discover.APOD_ARCHIVE: ARCHIVE, entry: ENTRY}), datetime.date(2026, 11, 3))
    assert len(cards) == 1
    c = cards[0]
    assert c["title"] == "Comet PanSTARRs and Rainbow Meteor"                 # not "NASA Science"
    assert c["image_url"].endswith("CometMeteor.jpg/r.1280.jpeg")
    assert c["body"].startswith("Did this meteor come from that comet? Probably not. Although many meteors do")
    assert "Explanation" not in c["body"] and c["link"] == entry
    assert c["source_ref"] == "discover:apod:2026-11-03"


def test_no_card_until_todays_entry_is_posted():
    # The archive's newest is yesterday's: no card (yesterday's picture would show twice)
    assert discover.apod(fetch_from({discover.APOD_ARCHIVE: ARCHIVE}), datetime.date(2026, 11, 4)) == []


def test_a_day_without_a_picture_gives_no_card():
    entry = "https://science.nasa.gov/image-article/apod-2026-november-3-comet-panstarrs-and-rainbow-meteor/"
    video_day = ENTRY.replace('<meta property="og:image" content="https://assets.science.nasa.gov/apod/2026/november/'
                              'CometMeteor.jpg/r.1280.jpeg" />', "")
    assert discover.apod(fetch_from({discover.APOD_ARCHIVE: ARCHIVE, entry: video_day}), datetime.date(2026, 11, 3)) == []


def test_entry_dates_are_read_both_ways():
    assert discover.entry_date("apod-2026-november-3-comet") == datetime.date(2026, 11, 3)
    assert discover.entry_date("apod-29-october-2026-the-horns-of-venus") == datetime.date(2026, 10, 29)
    assert discover.entry_date("https://science.nasa.gov/image-article/apod/2026-october-26-m42/") == datetime.date(2026, 10, 26)
    assert discover.entry_date("apod-about") is None
