__all__ = (
    "SolarElongMaskBasisFunction",
    "ZenithMaskBasisFunction",
    "ZenithShadowMaskBasisFunction",
    "HaMaskBasisFunction",
    "MoonAvoidanceBasisFunction",
    "MapCloudBasisFunction",
    "PlanetMaskBasisFunction",
    "MaskAzimuthBasisFunction",
    "SolarElongationMaskBasisFunction",
    "AreaCheckMaskBasisFunction",
    "AltAzShadowMaskBasisFunction",
)

import warnings

import healpy as hp
import numpy as np

from rubin_scheduler.scheduler.basis_functions import BaseBasisFunction
from rubin_scheduler.scheduler.utils import HpInLsstFov, IntRounded
from rubin_scheduler.utils import Site, _angular_separation, _hpid2_ra_dec


class SolarElongMaskBasisFunction(BaseBasisFunction):
    """Mask regions larger than some solar elongation limit

    Parameters
    ----------
    elong_limit : float (45)
        The limit beyond which to mask (degrees)
    """

    def __init__(self, elong_limit=45.0, nside=32):
        super(SolarElongMaskBasisFunction, self).__init__(nside=nside)
        self.elong_limit = IntRounded(np.radians(elong_limit))
        self.result = np.zeros(hp.nside2npix(self.nside), dtype=float)

    def _calc_value(self, conditions, indx=None):
        result = self.result.copy()
        to_mask = np.where(IntRounded(conditions.solar_elongation) > self.elong_limit)[0]
        result[to_mask] = np.nan
        return result


class HaMaskBasisFunction(BaseBasisFunction):
    """Limit the sky based on hour angle

    Parameters
    ----------
    ha_min : float (None)
        The minimum hour angle to accept (hours)
    ha_max : float (None)
        The maximum hour angle to accept (hours)
    """

    def __init__(self, ha_min=None, ha_max=None, nside=32):
        super(HaMaskBasisFunction, self).__init__(nside=nside)
        self.ha_max = ha_max
        self.ha_min = ha_min
        self.result = np.zeros(hp.nside2npix(self.nside), dtype=float)

    def _calc_value(self, conditions, **kwargs):
        result = self.result.copy()

        if self.ha_min is not None:
            good = np.where(conditions.HA < (self.ha_min / 12.0 * np.pi))[0]
            result[good] = np.nan
        if self.ha_max is not None:
            good = np.where(conditions.HA > (self.ha_max / 12.0 * np.pi))[0]
            result[good] = np.nan

        return result


class AreaCheckMaskBasisFunction(BaseBasisFunction):
    """Take a list of other mask basis functions, and do an additional
    check for area available"""

    def __init__(self, bf_list, nside=32, min_area=1000.0):
        super(AreaCheckMaskBasisFunction, self).__init__(nside=nside)
        self.bf_list = bf_list
        self.result = np.zeros(hp.nside2npix(self.nside), dtype=float)
        self.min_area = min_area

    def check_feasibility(self, conditions):
        result = True
        for bf in self.bf_list:
            if not bf.check_feasibility(conditions):
                return False

        area_map = self.result.copy()
        for bf in self.bf_list:
            area_map *= bf(conditions)

        good_pix = np.where(area_map == 0)[0]
        if hp.nside2pixarea(self.nside, degrees=True) * good_pix.size < self.min_area:
            result = False
        return result

    def _calc_value(self, conditions, **kwargs):
        result = self.result.copy()
        for bf in self.bf_list:
            result *= bf(conditions)
        return result


class SolarElongationMaskBasisFunction(BaseBasisFunction):
    """Mask things at various solar elongations

    Parameters
    ----------
    min_elong : float (0)
        The minimum solar elongation to consider (degrees).
    max_elong : float (60.)
        The maximum solar elongation to consider (degrees).
    """

    def __init__(self, min_elong=0.0, max_elong=60.0, nside=None, penalty=np.nan):
        super(SolarElongationMaskBasisFunction, self).__init__(nside=nside)
        self.min_elong = np.radians(min_elong)
        self.max_elong = np.radians(max_elong)
        self.penalty = penalty
        self.result = np.empty(hp.nside2npix(self.nside), dtype=float)
        self.result.fill(self.penalty)

    def _calc_value(self, conditions, indx=None):
        result = self.result.copy()
        in_range = np.where(
            (IntRounded(conditions.solar_elongation) >= IntRounded(self.min_elong))
            & (IntRounded(conditions.solar_elongation) <= IntRounded(self.max_elong))
        )[0]
        result[in_range] = 1
        return result


class ZenithMaskBasisFunction(BaseBasisFunction):
    """Just remove the area near zenith.

    Parameters
    ----------
    min_alt : float (20.)
        The minimum possible altitude (degrees)
    max_alt : float (82.)
        The maximum allowed altitude (degrees)
    """

    def __init__(self, min_alt=20.0, max_alt=82.0, nside=None):
        super(ZenithMaskBasisFunction, self).__init__(nside=nside)
        self.update_on_newobs = False
        self.min_alt = np.radians(min_alt)
        self.max_alt = np.radians(max_alt)
        self.result = np.empty(hp.nside2npix(self.nside), dtype=float).fill(self.penalty)

    def _calc_value(self, conditions, indx=None):
        result = self.result.copy()
        alt_limit = np.where(
            (IntRounded(conditions.alt) > IntRounded(self.min_alt))
            & (IntRounded(conditions.alt) < IntRounded(self.max_alt))
        )[0]
        result[alt_limit] = 1
        return result


class PlanetMaskBasisFunction(BaseBasisFunction):
    """Mask the bright planets

    Parameters
    ----------
    mask_radius : float (3.5)
        The radius to mask around a planet (degrees).
    planets : list of str (None)
        A list of planet names to mask. Defaults to ['venus', 'mars',
        'jupiter']. Not including Saturn because it moves really slow
        and has average apparent mag of ~0.4, so fainter than Vega.

    """

    def __init__(self, mask_radius=3.5, planets=None, nside=None, scale=1e5):
        super(PlanetMaskBasisFunction, self).__init__(nside=nside)
        if planets is None:
            planets = ["venus", "mars", "jupiter"]
        self.planets = planets
        self.mask_radius = np.radians(mask_radius)
        self.result = np.zeros(hp.nside2npix(nside))
        # set up a kdtree. Could maybe use healpy.query_disc instead.
        self.in_fov = HpInLsstFov(nside=nside, fov_radius=mask_radius, scale=scale)

    def _calc_value(self, conditions, indx=None):
        result = self.result.copy()
        for pn in self.planets:
            indices = self.in_fov(
                np.max(conditions.planet_positions[pn + "_RA"]),
                np.max(conditions.planet_positions[pn + "_dec"]),
            )
            result[indices] = np.nan

        return result


class AltAzShadowMaskBasisFunction(BaseBasisFunction):
    """Mask out range altitudes and azimuths, then extend the
    mask so if observations are taken in pairs, the second in the pair will
    not have moved into a masked region.

    Masks any alt/az regions as specified by the conditions object, or
    values as provided by the kwargs. If both are provided,
    the most restrictive of each will be used.
    This mask is then extended using `shadow minutes`.

    Parameters
    ----------
    nside : `int` or None
        HEALpix nside. Default None will look up the package-wide default.
    min_alt : `float`
        Minimum altitude to apply to the mask. Default 20 (degrees).
    max_alt : `float`
        Maximum altitude to allow. Default 82 (degrees).
    min_az : `float`
        Minimum azimuth value to apply to the mask. Default 0 (degrees).
    max_az : `float`
        Maximum azimuth value to apply to the mask. Default 360 (degrees).
    shadow_minutes : `float`
        How long to extend masked area in longitude. Default 40 (minutes).
    pad : `float`
        Pad the conditions alt/az limits by this amount (degrees).
        Default corresponds to approximately the radius of the fov.
    """

    def __init__(
        self, nside=None, min_alt=20.0, max_alt=82.0, min_az=0, max_az=360, shadow_minutes=40.0, pad=2.0
    ):
        super().__init__(nside=nside)
        self.min_alt = np.radians(min_alt)
        self.max_alt = np.radians(max_alt)
        self.min_az = np.radians(min_az)
        self.max_az = np.radians(max_az)
        self.shadow_time = shadow_minutes / 60.0 / 24.0  # To days
        self.pad = np.radians(pad)

    def _calc_value(self, conditions, indx=None):
        # Mask everything to start
        result = np.zeros(hp.nside2npix(self.nside), dtype=float) + np.nan

        in_range_alt = np.zeros(hp.nside2npix(self.nside), dtype=int)
        in_range_az = np.zeros(hp.nside2npix(self.nside), dtype=int)

        # Compute the alt,az values in the future. Use the conditions object
        # so the results are cached and can be used by other surveys is
        # needed. Technically this could fail if the masked region is
        # very narrow or shadow time is very large.
        future_alt, future_az = conditions.future_alt_az(np.max(conditions.mjd + self.shadow_time))

        # Find minimum alt limits
        min_alt = IntRounded(np.max([conditions.tel_alt_min + self.pad, self.min_alt]))
        max_alt = IntRounded(np.min([conditions.tel_alt_max - self.pad, self.max_alt]))

        # Check allowable altitude range against current and future alt values
        good = np.where((IntRounded(conditions.alt) >= min_alt) & (IntRounded(conditions.alt) <= max_alt))[0]

        in_range_alt[good] += 1
        good = np.where((IntRounded(future_alt) >= min_alt) & (IntRounded(future_alt) <= max_alt))[0]
        in_range_alt[good] += 1

        # Check allowable azimuth range against azimuth values
        # note that % (mod) is not supported for IntRounded
        two_pi = 2 * np.pi
        itwo_pi = IntRounded(two_pi)
        if IntRounded(conditions.tel_az_max) - IntRounded(conditions.tel_az_min) >= itwo_pi:
            in_range1 = np.zeros(len(conditions.az)) + 2
        else:
            azr = (conditions.tel_az_max - conditions.tel_az_min) % (two_pi) - 2 * self.pad
            # Check current and future
            in_range1 = np.where((conditions.az - conditions.tel_az_min - self.pad) % (two_pi) <= azr, 1, 0)
            in_range1 += np.where((future_az - conditions.tel_az_min - self.pad) % (two_pi) <= azr, 1, 0)
        if IntRounded(self.max_az) - IntRounded(self.min_az) >= itwo_pi:
            in_range2 = np.zeros(len(conditions.az)) + 2
        else:
            azr = (self.max_az - self.min_az) % (two_pi)
            in_range2 = np.where((conditions.az - self.min_az) % (two_pi) <= azr, 1, 0)
            in_range2 += np.where((future_az - self.min_az) % (two_pi) <= azr, 1, 0)
        in_range_az[np.where((in_range1 > 1) & (in_range2 > 1))] = 2

        passed_all = np.where((in_range_alt > 1) & (in_range_az > 1))[0]
        # Unmask the parts of the sky which are and will remain in range
        result[passed_all] = 0

        return result


class ZenithShadowMaskBasisFunction(BaseBasisFunction):
    """Mask the zenith, and things that will soon pass near zenith.
    Useful for making sure observations will not be too close to zenith
    when they need to be observed again (e.g. for a pair).

    Parameters
    ----------
    min_alt : float (20.)
        The minimum alititude to alow. Everything lower is masked. (degrees)
    max_alt : float (82.)
        The maximum altitude to alow. Everything higher is masked. (degrees)
    shadow_minutes : float (40.)
        Mask anything that will pass through the max alt in the next
        shadow_minutes time. (minutes)
    """

    def __init__(
        self,
        nside=None,
        min_alt=20.0,
        max_alt=82.0,
        shadow_minutes=40.0,
        penalty=np.nan,
        site="LSST",
    ):
        warnings.warn(
            "Deprecating ZenithShadowMaskBasisFunction in favor of AltAzShadowMaskBasisFunction.",
            DeprecationWarning,
        )

        super(ZenithShadowMaskBasisFunction, self).__init__(nside=nside)
        self.update_on_newobs = False

        self.penalty = penalty

        self.min_alt = np.radians(min_alt)
        self.max_alt = np.radians(max_alt)
        self.ra, self.dec = _hpid2_ra_dec(nside, np.arange(hp.nside2npix(nside)))
        self.shadow_minutes = np.radians(shadow_minutes / 60.0 * 360.0 / 24.0)
        # Compute the declination band where things could drift into zenith
        self.decband = np.zeros(self.dec.size, dtype=float)
        self.zenith_radius = np.radians(90.0 - max_alt) / 2.0
        site = Site(name=site)
        self.lat_rad = site.latitude_rad
        self.lon_rad = site.longitude_rad
        self.decband[
            np.where(
                (IntRounded(self.dec) < IntRounded(self.lat_rad + self.zenith_radius))
                & (IntRounded(self.dec) > IntRounded(self.lat_rad - self.zenith_radius))
            )
        ] = 1

        self.result = np.empty(hp.nside2npix(self.nside), dtype=float)
        self.result.fill(self.penalty)

    def _calc_value(self, conditions, indx=None):
        result = self.result.copy()
        alt_limit = np.where(
            (IntRounded(conditions.alt) > IntRounded(self.min_alt))
            & (IntRounded(conditions.alt) < IntRounded(self.max_alt))
        )[0]
        result[alt_limit] = 1
        to_mask = np.where(
            (IntRounded(conditions.HA) > IntRounded(2.0 * np.pi - self.shadow_minutes - self.zenith_radius))
            & (self.decband == 1)
        )
        result[to_mask] = np.nan
        return result


class MoonAvoidanceBasisFunction(BaseBasisFunction):
    """Avoid looking too close to the moon.

    Parameters
    ----------
    moon_distance: float (30.)
        Minimum allowed moon distance. (degrees)

    XXX--TODO:  This could be a more complicated function of filter
    and moon phase.
    """

    def __init__(self, nside=None, moon_distance=30.0):
        super(MoonAvoidanceBasisFunction, self).__init__(nside=nside)
        self.update_on_newobs = False

        self.moon_distance = IntRounded(np.radians(moon_distance))
        self.result = np.ones(hp.nside2npix(self.nside), dtype=float)

    def _calc_value(self, conditions, indx=None):
        result = self.result.copy()

        angular_distance = _angular_separation(
            conditions.az, conditions.alt, conditions.moon_az, conditions.moon_alt
        )

        result[IntRounded(angular_distance) < self.moon_distance] = np.nan

        return result


class BulkCloudBasisFunction(BaseBasisFunction):
    """Mark healpixels on a map if their cloud values are greater than
    the same healpixels on a maximum cloud map.

    Parameters
    ----------
    nside: int (default_nside)
        The healpix resolution.
    max_cloud_map : numpy array (None)
        A healpix map showing the maximum allowed cloud values for all
        points on the sky
    out_of_bounds_val : float (10.)
        Point value to give regions where there are no observations
        requested
    """

    def __init__(self, nside=None, max_cloud_map=None, max_val=0.7, out_of_bounds_val=np.nan):
        super(BulkCloudBasisFunction, self).__init__(nside=nside)
        self.update_on_newobs = False

        if max_cloud_map is None:
            self.max_cloud_map = np.zeros(hp.nside2npix(nside), dtype=float) + max_val
        else:
            self.max_cloud_map = max_cloud_map
        self.out_of_bounds_area = np.where(self.max_cloud_map > 1.0)[0]
        self.out_of_bounds_val = out_of_bounds_val
        self.result = np.ones(hp.nside2npix(self.nside))

    def _calc_value(self, conditions, indx=None):
        """
        Parameters
        ----------
        indx : list (None)
            Index values to compute, if None, full map is computed
        Returns
        -------
        Healpix map where pixels with a cloud value greater than the
        max_cloud_map value are marked as unseen.
        """

        result = self.result.copy()

        clouded = np.where(self.max_cloud_map <= conditions.bulk_cloud)
        result[clouded] = self.out_of_bounds_val

        return result


class MapCloudBasisFunction(BaseBasisFunction):
    """Mark healpixels on a map if their cloud values are greater than
    the same healpixels on a maximum cloud map. Currently a placeholder for
    when the telemetry stream can include a full sky cloud map.

    Parameters
    ----------
    nside: int (default_nside)
        The healpix resolution.
    max_cloud_map : numpy array (None)
        A healpix map showing the maximum allowed cloud values for all
        points on the sky
    out_of_bounds_val : float (10.)
        Point value to give regions where there are no observations
        requested
    """

    def __init__(self, nside=None, max_cloud_map=None, max_val=0.7, out_of_bounds_val=np.nan):
        super(BulkCloudBasisFunction, self).__init__(nside=nside)
        self.update_on_newobs = False

        if max_cloud_map is None:
            self.max_cloud_map = np.zeros(hp.nside2npix(nside), dtype=float) + max_val
        else:
            self.max_cloud_map = max_cloud_map
        self.out_of_bounds_area = np.where(self.max_cloud_map > 1.0)[0]
        self.out_of_bounds_val = out_of_bounds_val
        self.result = np.ones(hp.nside2npix(self.nside))

    def _calc_value(self, conditions, indx=None):
        """
        Parameters
        ----------
        indx : list (None)
            Index values to compute, if None, full map is computed
        Returns
        -------
        Healpix map where pixels with a cloud value greater than the
        max_cloud_map value are marked as unseen.
        """

        result = self.result.copy()

        clouded = np.where(self.max_cloud_map <= conditions.bulk_cloud)
        result[clouded] = self.out_of_bounds_val

        return result


class MaskAzimuthBasisFunction(BaseBasisFunction):
    """Mask pixels based on azimuth"""

    def __init__(self, nside=None, out_of_bounds_val=np.nan, az_min=0.0, az_max=180.0):
        super(MaskAzimuthBasisFunction, self).__init__(nside=nside)
        self.az_min = IntRounded(np.radians(az_min))
        self.az_max = IntRounded(np.radians(az_max))
        self.out_of_bounds_val = out_of_bounds_val
        self.result = np.ones(hp.nside2npix(self.nside))

    def _calc_value(self, conditions, indx=None):
        to_mask = np.where(
            (IntRounded(conditions.az) > self.az_min) & (IntRounded(conditions.az) < self.az_max)
        )[0]
        result = self.result.copy()
        result[to_mask] = self.out_of_bounds_val

        return result
