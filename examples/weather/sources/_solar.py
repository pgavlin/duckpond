"""Local sunrise/sunset computation -- the standard sunrise equation, no network.

Sunrise, sunset, and day length are a deterministic function of latitude, longitude, and
date, so this example computes them rather than calling a rate-limited external API. The
formulas follow the NOAA / Wikipedia "sunrise equation" and are accurate to about a minute
at the mid-latitudes covered here.
"""
import math

DAY = 86400
_EPOCH_JD = 2440587.5                     # Julian date of the Unix epoch
_J2000 = 2451545.0                        # Julian date of 2000-01-01 12:00 UTC
_OBLIQUITY = math.radians(23.4397)        # Earth's axial tilt
_HORIZON = math.radians(-0.833)           # sun altitude at sunrise/sunset (refraction + radius)


def sun_times(lat: float, lon: float, day_epoch: int) -> tuple[int, int, int] | None:
    """Sunrise, sunset, and day length for the UTC day containing day_epoch.

    Returns (sunrise, sunset, day_length_s) as UTC epoch seconds, or None at the poles on a
    day the sun never crosses the horizon.
    """
    j_date = day_epoch / DAY + _EPOCH_JD
    n = math.ceil(j_date - _J2000 + 0.0008)            # whole days since J2000, leap-corrected
    j_star = n + -lon / 360.0                           # mean solar time (west longitude positive)
    m = math.radians((357.5291 + 0.98560028 * j_star) % 360)            # solar mean anomaly
    c = 1.9148 * math.sin(m) + 0.0200 * math.sin(2 * m) + 0.0003 * math.sin(3 * m)
    lam = math.radians((math.degrees(m) + c + 282.9372) % 360)          # ecliptic longitude
    j_transit = _J2000 + j_star + 0.0053 * math.sin(m) - 0.0069 * math.sin(2 * lam)
    sin_decl = math.sin(lam) * math.sin(_OBLIQUITY)
    decl = math.asin(sin_decl)
    phi = math.radians(lat)
    cos_w = (math.sin(_HORIZON) - math.sin(phi) * sin_decl) / (math.cos(phi) * math.cos(decl))
    if not -1.0 <= cos_w <= 1.0:
        return None                                    # polar day or polar night
    w0 = math.degrees(math.acos(cos_w))
    rise = round((j_transit - w0 / 360.0 - _EPOCH_JD) * DAY)
    sett = round((j_transit + w0 / 360.0 - _EPOCH_JD) * DAY)
    return rise, sett, sett - rise
