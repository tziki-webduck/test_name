"""Shabbat times (candle lighting / havdalah) computed from the sun position.

Uses the NOAA "Almanac for Computers" sunrise/sunset algorithm, accurate to
about a minute, which is plenty for driving air conditioners.
"""

import math
from datetime import date, datetime, timedelta, timezone

SUNSET_ZENITH = 90.833
TZEIT_ZENITH = 98.5  # sun 8.5 degrees below the horizon (3 small stars)

# name: (latitude, longitude, minutes of candle lighting before sunset)
CITIES = {
    "ירושלים": (31.778, 35.235, 40),
    "תל אביב": (32.085, 34.781, 18),
    "חיפה": (32.794, 34.989, 30),
    "באר שבע": (31.252, 34.791, 18),
    "פתח תקווה": (32.087, 34.886, 20),
    "בני ברק": (32.081, 34.833, 22),
    "אשדוד": (31.804, 34.655, 18),
    "נתניה": (32.332, 34.860, 18),
    "ראשון לציון": (31.964, 34.804, 18),
    "מודיעין": (31.898, 35.010, 20),
    "בית שמש": (31.747, 34.988, 30),
    "צפת": (32.965, 35.496, 30),
    "טבריה": (32.794, 35.531, 20),
    "אילת": (29.558, 34.952, 18),
}


def _sun_event_utc(day, lat, lon, zenith, rising):
    n = day.timetuple().tm_yday
    lng_hour = lon / 15.0
    t = n + ((6 if rising else 18) - lng_hour) / 24.0
    m = 0.9856 * t - 3.289
    l = (m + 1.916 * math.sin(math.radians(m)) + 0.020 * math.sin(math.radians(2 * m)) + 282.634) % 360
    ra = math.degrees(math.atan(0.91764 * math.tan(math.radians(l)))) % 360
    ra = (ra + (math.floor(l / 90) * 90 - math.floor(ra / 90) * 90)) / 15.0
    sin_dec = 0.39782 * math.sin(math.radians(l))
    cos_dec = math.cos(math.asin(sin_dec))
    cos_h = (math.cos(math.radians(zenith)) - sin_dec * math.sin(math.radians(lat))) / (
        cos_dec * math.cos(math.radians(lat)))
    if not -1 <= cos_h <= 1:
        return None
    h = math.degrees(math.acos(cos_h))
    if rising:
        h = 360 - h
    ut = (h / 15.0 + ra - 0.06571 * t - 6.622 - lng_hour) % 24
    base = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    return base + timedelta(hours=ut)


def sunset(day, lat, lon):
    return _sun_event_utc(day, lat, lon, SUNSET_ZENITH, rising=False)


def tzeit(day, lat, lon):
    return _sun_event_utc(day, lat, lon, TZEIT_ZENITH, rising=False)


def shabbat_window(ref, settings, tz):
    """Return (start, end) of the Shabbat that is current at `ref`, or the next one.

    `ref` is an aware datetime. Start is candle lighting on Friday, end is
    havdalah on Saturday (tzeit + extra minutes, or sunset + fixed minutes).
    """
    lat, lon = settings["lat"], settings["lon"]
    candle = int(settings.get("candle_minutes", 18))
    havdalah_mode = settings.get("havdalah_mode", "tzeit")
    havdalah_extra = int(settings.get("havdalah_minutes", 0))

    local = ref.astimezone(tz)
    # weekday(): Monday=0 ... Friday=4, Saturday=5
    days_back = (local.weekday() - 4) % 7
    friday = local.date() - timedelta(days=days_back)
    for _ in range(2):
        start = sunset(friday, lat, lon) - timedelta(minutes=candle)
        saturday = friday + timedelta(days=1)
        if havdalah_mode == "sunset":
            end = sunset(saturday, lat, lon) + timedelta(minutes=havdalah_extra or 40)
        else:
            end = tzeit(saturday, lat, lon) + timedelta(minutes=havdalah_extra)
        start, end = start.astimezone(tz), end.astimezone(tz)
        if ref < end:
            return start, end
        friday += timedelta(days=7)
    return start, end


def is_shabbat(ref, settings, tz):
    start, end = shabbat_window(ref, settings, tz)
    return start <= ref < end


def candle_lighting_today(day: date, settings, tz):
    return (sunset(day, settings["lat"], settings["lon"]) -
            timedelta(minutes=int(settings.get("candle_minutes", 18)))).astimezone(tz)
