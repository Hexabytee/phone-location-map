"""Phone number location lookup and map generator.

Parses a phone number, shows carrier/region info, geocodes the region
via OpenCage, and renders an interactive Folium map.

Usage:
    python main.py --phone "+14155552671"
    python main.py --phone "+14155552671" --output sample_map.html

The OpenCage API key must be provided via the OPENCAGE_API_KEY
environment variable (see .env.example). A legacy fallback reads the
phone number from myphone.py for backward compatibility.

Note: phonenumbers only maps number prefixes to a region (country /
city). This is NOT live GPS tracking.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

import folium
import phonenumbers
from dotenv import load_dotenv
from opencage.geocoder import OpenCageGeocode
from phonenumbers import carrier, geocoder, timezone

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

DEFAULT_OUTPUT = "my_location.html"
DEFAULT_LANG = "en"


def load_api_key() -> str:
    """Return the OpenCage API key from the environment or exit."""
    key = os.getenv("OPENCAGE_API_KEY", "").strip().strip('"').strip("'")
    if not key:
        log.error(
            "OPENCAGE_API_KEY is not set. "
            "Copy .env.example to .env and add your key, "
            "or set it in the environment."
        )
        sys.exit(2)
    return key


def resolve_phone_number(args_phone: str | None) -> str:
    """Return the phone number from CLI args or legacy myphone.py fallback."""
    if args_phone:
        return args_phone.strip()
    try:
        from myphone import number  # type: ignore

        if number and str(number).strip() and "+" not in str(number):
            log.warning("Number in myphone.py should be in E.164 format, e.g. +919876543210")
        log.warning("Using phone number from myphone.py (deprecated). Prefer --phone.")
        if str(number).strip():
            return str(number).strip()
    except ImportError:
        pass
    log.error("No phone number provided. Use: python main.py --phone \"+919876543210\"")
    sys.exit(2)
    return ""  # unreachable, keeps type checkers happy


def parse_phone_number(raw: str) -> phonenumbers.PhoneNumber:
    """Parse and validate a phone number, exiting with a clear message on failure."""
    try:
        parsed = phonenumbers.parse(raw, None)
    except phonenumbers.NumberParseException as exc:
        log.error("Could not parse phone number %r: %s", raw, exc)
        sys.exit(2)
    if not phonenumbers.is_possible_number(parsed):
        log.error("Phone number %r is not a possible number. Check country code and length.", raw)
        sys.exit(2)
    if not phonenumbers.is_valid_number(parsed):
        log.error("Phone number %r is not a valid assigned number.", raw)
        sys.exit(2)
    return parsed


def get_phone_info(parsed: phonenumbers.PhoneNumber, lang: str = DEFAULT_LANG) -> dict:
    """Collect display info for an already-validated parsed number."""
    number_type = phonenumbers.number_type(parsed)
    type_name = {
        phonenumbers.PhoneNumberType.MOBILE: "mobile",
        phonenumbers.PhoneNumberType.FIXED_LINE: "fixed line",
        phonenumbers.PhoneNumberType.FIXED_LINE_OR_MOBILE: "fixed line or mobile",
        phonenumbers.PhoneNumberType.TOLL_FREE: "toll free",
        phonenumbers.PhoneNumberType.PREMIUM_RATE: "premium rate",
        phonenumbers.PhoneNumberType.VOIP: "voip",
    }.get(number_type, str(int(number_type)))

    return {
        "e164": phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164),
        "international": phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.INTERNATIONAL),
        "country_code": parsed.country_code,
        "national": phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.NATIONAL),
        "region_code": phonenumbers.region_code_for_number(parsed) or "unknown",
        "location": geocoder.description_for_number(parsed, lang) or "",
        "carrier_name": carrier.name_for_number(parsed, lang) or "unknown",
        "timezones": timezone.time_zones_for_number(parsed),
        "number_type": type_name,
        "valid": phonenumbers.is_valid_number(parsed),
    }


def geocode_location(query: str, api_key: str) -> tuple[float, float, str, dict]:
    """Geocode a place name via OpenCage. Returns (lat, lng, formatted, components)."""
    if not query.strip():
        log.error(
            "Phone prefix has no geographic region (e.g. toll-free / VoIP). "
            "Cannot geocode an empty location."
        )
        sys.exit(1)

    client = OpenCageGeocode(api_key)
    try:
        results = client.geocode(query, no_annotations=0, limit=1)
    except Exception as exc:  # OpenCage raises its own exceptions + network errors
        log.error("Geocoding request failed for %r: %s", query, exc)
        sys.exit(1)

    if not results:
        log.error("No geocoding results found for %r.", query)
        sys.exit(1)

    first = results[0]
    try:
        lat = float(first["geometry"]["lat"])
        lng = float(first["geometry"]["lng"])
    except (KeyError, TypeError, ValueError):
        log.error("Geocoding response for %r is missing geometry: %r", query, first)
        sys.exit(1)

    formatted = str(first.get("formatted", query))
    components = dict(first.get("components", {}))
    return lat, lng, formatted, components


def choose_zoom(components: dict, explicit: int | None) -> int:
    """Pick a sensible Folium zoom level based on geocode granularity."""
    if explicit is not None:
        return explicit
    keys = set(components.keys())
    if keys & {"city", "town", "village", "suburb", "neighbourhood", "postcode"}:
        return 10
    if keys & {"county", "state", "state_district", "province"}:
        return 7
    return 5  # country-level result (the common case for phone prefixes)


def create_map(
    lat: float,
    lng: float,
    title: str,
    popup_html: str,
    output: str,
    zoom: int,
) -> None:
    """Render a Folium map with a single marker."""
    fmap = folium.Map(location=[lat, lng], zoom_start=zoom)
    folium.Marker([lat, lng], popup=folium.Popup(popup_html, max_width=300), tooltip=title).add_to(fmap)
    fmap.save(output)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Look up a phone number's region/carrier and render it on a map."
    )
    parser.add_argument("--phone", help='Phone number in E.164 format, e.g. "+919876543210"')
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help=f"Output HTML map (default: {DEFAULT_OUTPUT})")
    parser.add_argument("--lang", default=DEFAULT_LANG, help="Language for region/carrier names (default: en)")
    parser.add_argument("--zoom", type=int, default=None, help="Override Folium zoom level (auto if omitted)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    api_key = load_api_key()

    raw_number = resolve_phone_number(args.phone)
    parsed = parse_phone_number(raw_number)
    info = get_phone_info(parsed, args.lang)

    print(f"Number:      {info['international']}")
    print(f"Region:      {info['region_code']}")
    print(f"Location:    {info['location'] or '(no geographic region)'}")
    print(f"Carrier:     {info['carrier_name']}")
    print(f"Type:        {info['number_type']}")
    if info["timezones"]:
        print(f"Timezone(s): {', '.join(info['timezones'])}")

    lat, lng, formatted, components = geocode_location(info["location"], api_key)
    print(f"Geocoded:    {formatted}")
    print(f"Lat/Lng:     {lat}, {lng}")

    zoom = choose_zoom(components, args.zoom)
    popup_html = (
        f"<b>{info['international']}</b><br>"
        f"{formatted}<br>"
        f"Carrier: {info['carrier_name']}<br>"
        f"{lat:.5f}, {lng:.5f}"
    )
    create_map(lat, lng, formatted, popup_html, args.output, zoom)
    print(f"Map saved as {args.output} (zoom={zoom})")
    print("Note: region is derived from the number prefix, not live GPS.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
