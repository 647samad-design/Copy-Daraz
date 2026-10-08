"""Tracking links for parcels.

Known couriers get a direct link to their tracking page; any other courier
falls back to 17TRACK, which looks a number up across 2,000+ carriers.
"""
from urllib.parse import quote

COURIERS = [
    ("DHL", "https://www.dhl.com/global-en/home/tracking/tracking-express.html?tracking-id={n}"),
    ("FedEx", "https://www.fedex.com/fedextrack/?trknbr={n}"),
    ("UPS", "https://www.ups.com/track?tracknum={n}"),
    ("USPS", "https://tools.usps.com/go/TrackConfirmAction?tLabels={n}"),
    ("Royal Mail", "https://www.royalmail.com/track-your-item#/tracking-results/{n}"),
    ("Canada Post", "https://www.canadapost-postescanada.ca/track-reperage/en#/search?searchFor={n}"),
    ("Australia Post", "https://auspost.com.au/mypost/track/details/{n}"),
    ("Aramex", "https://www.aramex.com/us/en/track/results?ShipmentNumber={n}"),
    ("DPD", "https://track.dpd.co.uk/search?reference={n}"),
    ("TNT", "https://www.tnt.com/express/en_gc/site/shipping-tools/tracking.html?searchType=con&cons={n}"),
    ("Emirates Post", "https://www.epg.gov.ae/_layouts/EPG/TrackAndTrace.aspx?ShipmentNumber={n}"),
    ("TCS", "https://www.tcsexpress.com/track/{n}"),
    ("Leopards", "https://www.leopardscourier.com/tracking?cn={n}"),
    ("Pakistan Post", "https://ep.gov.pk/track.asp?textfield={n}"),
    ("Blue Dart", "https://www.bluedart.com/tracking?trackFor=0&trackNo={n}"),
]
_BY_NAME = {name.lower(): url for name, url in COURIERS}
FALLBACK = "https://t.17track.net/en#nums={n}"


def courier_names():
    return [name for name, _ in COURIERS]


def tracking_link(courier, number, override=""):
    """URL where the customer can follow the parcel, or "" if there's no
    tracking number. ``override`` (a full https link) always wins."""
    if override and override.startswith("https://"):
        return override
    number = (number or "").strip()
    if not number:
        return ""
    template = _BY_NAME.get((courier or "").strip().lower(), FALLBACK)
    return template.format(n=quote(number, safe=""))
