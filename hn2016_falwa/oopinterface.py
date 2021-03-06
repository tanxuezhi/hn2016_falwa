from math import pi

import numpy as np
from scipy.interpolate import interp1d

from hn2016_falwa import utilities
from interpolate_fields import interpolate_fields
from compute_reference_states import compute_reference_states
from compute_lwa_and_barotropic_fluxes import compute_lwa_and_barotropic_fluxes


class QGField(object):

    """
    Local wave activity and flux analysis in quasi-geostrophic framework
    that can be used to reproduce the results in:
    Nakamura and Huang, Atmospheric Blocking as a Traffic Jam in the Jet Stream, Science (2018).
    Note that topography is assumed flat in this object.

    .. versionadded:: 0.3.0

    Parameters
    ----------
    xlon : numpy.array
           Array of evenly-spaced longitude (in degree) of size nlon.
    ylat : numpy.array
           Array of evenly-spaced latitude (in degree) of size nlat.
    plev : numpy.
           Array of pressure level (in hPa) of size nlev.
    u_field : numpy.ndarray
           Three-dimensional array of zonal wind field (in m/s) of dimension [nlev, nlat, nlon].
    v_field : numpy.ndarray
           Three-dimensional array of meridional wind field (in m/s) of dimension [nlev, nlat, nlon].
    t_field : numpy.ndarray
           Three-dimensional array of temperature field (in K) of dimension [nlev, nlat, nlon].
    kmax : int, optional
           Dimension of uniform pseudoheight grids used for interpolation.
    maxit : int, optional
           Number of iteration by the Successive over-relaxation (SOR) solver to compute the reference states.
    dz : float, optional
           Size of uniform pseudoheight grids (in meters).
    prefactor : float, optional
           Vertical air density summed over height.
    npart : int, optional
           Number of partitions used to compute equivalent latitude.
           If not initialized, it will be set to nlat.
    tol : float, optional
           Tolerance that defines the convergence of solution in SOR solver.
    rjac : float, optional
           Spectral radius of the Jacobi iteration in the SOR solver.
    scale_height : float, optional
           Scale height of the atmosphere in meters. Default = 7000.
    cp : float, optional
           Heat capacity of dry air in J/kg-K. Default = 1004.
    dry_gas_constant : float, optional
           Gas constant for dry air in J/kg-K. Default = 287.
    omega : float, optional
           Rotation rate of the earth in 1/s. Default = 7.29e-5.
    planet_radius : float, optional
           Radius of the planet in meters.
           Default = 6.378e+6 (Earth's radius).


    Examples
    --------
    >>> test_object = QGField(xlon, ylat, plev,
                             u_field, v_field, t_field)

    """

    def __init__(self, xlon, ylat, plev,
                 u_field, v_field, t_field,
                 kmax=49, maxit=100000, dz=1000., prefactor=6500.,
                 npart=None, tol=1.e-5, rjac=0.95,
                 scale_height=7000., cp=1004., dry_gas_constant=287.,
                 omega=7.29e-5, planet_radius=6.378e+6):

        """Create a QGField object.
        This only initialize the attributes of the object. Analysis and
        computation are done by calling various methods.
        """

        # Check if ylat is in ascending order and include the equator
        if np.diff(ylat)[0] < 0:
            raise TypeError("ylat must be in ascending order")
        # Even grid
        if (ylat.size % 2 == 0) & (sum(ylat == 0.0) == 0):
            self.ylat_no_equator = ylat
            self.ylat = np.linspace(-90., 90., ylat.size+1,
                                    endpoint=True)
            self.equator_idx = \
                np.argwhere(self.ylat == 0)[0][0] + 1
            # Fortran indexing starts from 1
            self.need_latitude_interpolation = True
            # raise TypeError("ylat must include the equator (i.e. degree 0)")
        # Odd grid
        elif sum(ylat == 0) == 1:
            self.ylat_no_equator = None
            self.ylat = ylat
            self.equator_idx = np.argwhere(ylat == 0)[0][0] + 1
            # Fortran indexing starts from 1
            self.need_latitude_interpolation = False
        else:
            raise TypeError(
                "There are more than 1 grid point with latitude 0."
            )
        self.clat = np.abs(np.cos(np.deg2rad(self.ylat)))

        # === Check if plev is in decending order ===
        if np.diff(plev)[0] > 0:
            raise TypeError("plev must be in decending order")
        else:
            self.plev = plev
        self.xlon = xlon

        # === Check the shape of wind/temperature fields ===
        self.nlev, nlat, self.nlon = plev.size, ylat.size, xlon.size
        expected_dimension = (self.nlev, nlat, self.nlon)
        if u_field.shape != expected_dimension:
            raise TypeError(
                "Incorrect dimension of u_field. Expected dimension: {}"
                .format(expected_dimension)
            )
        if v_field.shape != expected_dimension:
            raise TypeError(
                "Incorrect dimension of v_field. Expected dimension: {}"
                .format(expected_dimension)
            )
        if t_field.shape != expected_dimension:
            raise TypeError(
                "Incorrect dimension of t_field. Expected dimension: {}"
                .format(expected_dimension)
            )

        # === Do Interpolation on latitude grid if needed ===
        self.nlat = self.ylat.size
        if self.need_latitude_interpolation:
            interp_u = interp1d(
                self.ylat_no_equator, u_field, axis=1, fill_value="extrapolate"
            )
            interp_v = interp1d(
                self.ylat_no_equator, v_field, axis=1, fill_value="extrapolate"
            )
            interp_t = interp1d(
                self.ylat_no_equator, t_field, axis=1, fill_value="extrapolate"
            )
            self.u_field = interp_u(self.ylat)
            self.v_field = interp_v(self.ylat)
            self.t_field = interp_t(self.ylat)
        else:
            self.u_field = u_field
            self.v_field = v_field
            self.t_field = t_field

        # === To be computed ===
        self.dphi = np.deg2rad(180./(self.nlat-1))
        self.dlambda = np.deg2rad(self.xlon[1] - self.xlon[0])

        if npart is None:
            self.npart = self.nlat
        else:
            self.npart = npart
        self.height = np.array([i * dz for i in range(kmax)])

        # === Parameters ===
        self.kmax = kmax
        self.maxit = maxit
        self.dz = dz
        self.prefactor = prefactor
        self.tol = tol
        self.rjac = rjac

        # === Constants ===
        self.scale_height = scale_height
        self.cp = cp
        self.dry_gas_constant = dry_gas_constant
        self.omega = omega
        self.planet_radius = planet_radius

        # === Variables that will be computed in methods ===
        self.qgpv_temp = None
        self.interpolated_u_temp = None
        self.interpolated_v_temp = None
        self.interpolated_theta_temp = None
        self.static_stability = None
        self.qref_temp = None
        self.uref_temp = None
        self.ptref_temp = None

    def get_latitude_dim(self):
        """
        Return the latitude dimension of the input data.
        """
        if self.need_latitude_interpolation:
            return self.nlat - 1
        else:
            return self.nlat

    def _interp_back(
        self,
        field,
        interp_from,
        interp_to,
        which_axis=1
    ):
        '''
        Internal function to interpolate the results from odd grid to even grid. If the initial input to the QGField object is an odd grid, error will be raised.

        '''

        if self.ylat_no_equator is None:
            raise TypeError("No need for such interpolation.")
        else:
            return interp1d(
                interp_from, field, axis=which_axis, bounds_error=False,
                fill_value='extrapolate'
            )(interp_to)

    def interpolate_fields(self):
        """
        Interpolate zonal wind, maridional wind, and potential temperature field onto the uniform pseudoheight grids, and compute QGPV on the same grids.

        Returns
        -------

        qgpv : numpy.ndarray
            Three-dimensional array of quasi-geostrophic potential vorticity (QGPV) with dimension = [kmax, nlat, nlon]

        interpolated_u : numpy.ndarray
            Three-dimensional array of interpolated zonal wind with dimension = [kmax, nlat, nlon]

        interpolated_v : numpy.ndarray
            Three-dimensional array of interpolated meridional wind with dimension = [kmax, nlat, nlon]

        interpolated_theta : numpy.ndarray
            Three-dimensional array of interpolated potential temperature with dimension = [kmax, nlat, nlon]

        static_stability : numpy.array
            One-dimension array of interpolated static stability with dimension = kmax


        Examples
        --------

        >>> qgpv, interpolated_u, interpolated_v,
            interpolated_theta, static_stability
            = test_object.interpolate_fields()

        """

        if self.qref_temp is None:

            # === Interpolate fields and obtain qgpv ===
            self.qgpv_temp, \
                self.interpolated_u_temp, \
                self.interpolated_v_temp, \
                self.interpolated_theta_temp, \
                self.static_stability = \
                interpolate_fields(np.swapaxes(self.u_field, 0, 2),
                                   np.swapaxes(self.v_field, 0, 2),
                                   np.swapaxes(self.t_field, 0, 2),
                                   self.plev,
                                   self.height,
                                   self.planet_radius,
                                   self.omega,
                                   self.dz,
                                   self.scale_height,
                                   self.dry_gas_constant,
                                   self.cp)

            self.qgpv = np.swapaxes(self.qgpv_temp, 0, 2)
            self.interpolated_u = np.swapaxes(self.interpolated_u_temp, 0, 2)
            self.interpolated_v = np.swapaxes(self.interpolated_v_temp, 0, 2)
            self.interpolated_theta = np.swapaxes(
                self.interpolated_theta_temp, 0, 2
            )

        if self.need_latitude_interpolation:
            # Interpolate back to original grid
            self.qgpv = self._interp_back(
                self.qgpv, self.ylat, self.ylat_no_equator
            )
            self.interpolated_u = self._interp_back(
                self.interpolated_u, self.ylat, self.ylat_no_equator
            )
            self.interpolated_v = self._interp_back(
                self.interpolated_v, self.ylat, self.ylat_no_equator
            )
            self.interpolated_theta = self._interp_back(
                self.interpolated_theta, self.ylat, self.ylat_no_equator
            )

        return self.qgpv, self.interpolated_u, self.interpolated_v, \
            self.interpolated_theta, self.static_stability

    def get_qgpv(self):
        """
        Retrieve the interpolated quasi-geostrophic potential vorticity field.
        """
        return self.qgpv

    def get_u(self):
        """
        Retrieve the interpolated zonal wind field [m/s].
        """
        return self.interpolated_u

    def get_v(self):
        """
        Retrieve the interpolated meridional wind field [m/s].
        """
        return self.interpolated_v

    def get_theta(self):
        """
        Retrieve the interpolated potential temperature field [K].
        """
        return self.interpolated_theta

    def get_static_stability(self):
        """
        Retrieve the interpolated static stability.
        """
        return self.static_stability

    def compute_reference_states(self, northern_hemisphere_results_only=True):

        """
        Compute the local wave activity and reference states of QGPV, zonal wind and potential temperature using a more stable inversion algorithm applied in Nakamura and Huang (2018, Science). The equation to be invert is equation (22) in supplementary materials of Huang and Nakamura (2017, GRL). In this version, only values in the Northern Hemisphere is computed.


        Parameters
        ----------
        northern_hemisphere_results_only : bool
               If true, arrays of size [kmax, nlat//2+1] will be returned. Otherwise, arrays of size [kmax, nlat] will be returned. This parameter is present since the current version (v0.3.1) of the package only return analysis in the northern hemisphere. Default: True.

        Returns
        -------

        qref : numpy.ndarray
            Two-dimensional array of reference state of quasi-geostrophic potential vorticity (QGPV) with dimension = [kmax, nlat, nlon] if northern_hemisphere_results_only=False, or dimension = [kmax, nlat//2+1, nlon] if northern_hemisphere_results_only=True

        uref : numpy.ndarray
            Two-dimensional array of reference state of zonal wind (Uref) with dimension = [kmax, nlat, nlon] if northern_hemisphere_results_only=False, or dimension = [kmax, nlat//2+1, nlon] if northern_hemisphere_results_only=True

        ptref : numpy.ndarray
            Two-dimensional array of reference state of potential temperature (Theta_ref) with dimension = [kmax, nlat, nlon] if northern_hemisphere_results_only=False, or dimension = [kmax, nlat//2+1, nlon] if northern_hemisphere_results_only=True

        Examples
        --------

        >>> qref, uref, ptref = test_object.compute_reference_states()

        """
        self.northern_hemisphere_results_only = \
            northern_hemisphere_results_only

        if self.qgpv_temp is None:
            self.interpolate_fields()

        if self.uref_temp is None:
            # === Compute reference states in Northern Hemisphere ===
            self.qref_temp, self.uref_temp, self.ptref_temp = \
                compute_reference_states(self.qgpv_temp,
                                         self.interpolated_u_temp,
                                         self.interpolated_theta_temp,
                                         self.static_stability,
                                         self.equator_idx,
                                         self.npart,
                                         self.maxit,
                                         self.planet_radius,
                                         self.omega,
                                         self.dz,
                                         self.tol,
                                         self.scale_height,
                                         self.dry_gas_constant,
                                         self.cp,
                                         self.rjac)

            self.qref_temp_right_unit = self.qref_temp * 2 * self.omega * \
                np.sin(np.deg2rad(self.ylat[(self.equator_idx - 1):,
                                  np.newaxis]))

            if self.northern_hemisphere_results_only:
                self.qref = np.swapaxes(self.qref_temp_right_unit, 0, 1)
                self.uref = np.swapaxes(self.uref_temp, 0, 1)
                self.ptref = np.swapaxes(self.ptref_temp, 0, 1)
                ylat_interp_from = self.ylat[-(self.qref.shape[1]):]
                if self.need_latitude_interpolation:
                    ylat_interp_to = self.ylat_no_equator[
                        -(self.ylat_no_equator.size)//2:
                   ]
            else:
                self.qref = \
                    np.hstack((np.zeros((self.kmax, self.equator_idx - 1)),
                               np.swapaxes(self.qref_temp_right_unit, 0, 1)))
                self.uref = \
                    np.hstack((np.zeros((self.kmax, self.equator_idx - 1)),
                               np.swapaxes(self.uref_temp, 0, 1)))
                self.ptref = \
                    np.hstack((np.zeros((self.kmax, self.equator_idx - 1)),
                               np.swapaxes(self.ptref_temp, 0, 1)))
                ylat_interp_from = self.ylat
                if self.need_latitude_interpolation:
                    ylat_interp_to = self.ylat_no_equator

            if self.need_latitude_interpolation:
                self.qref = self._interp_back(
                    self.qref, ylat_interp_from, ylat_interp_to
                )
                self.uref = self._interp_back(
                    self.uref, ylat_interp_from, ylat_interp_to
                )
                self.ptref = self._interp_back(
                    self.ptref, ylat_interp_from, ylat_interp_to
                )

        return self.qref, self.uref, self.ptref


    def get_qref(self):
        """
        Retrieve the QGPV reference state [1/s].
        """
        return self.qref


    def get_uref(self):
        """
        Retrieve the zonal wind reference state [m/s].
        """
        return self.uref


    def get_ptref(self):
        """
        Retrieve the potential temperature reference state [K].
        """
        return self.ptref


    def compute_lwa_and_barotropic_fluxes(
        self, northern_hemisphere_results_only=True
    ):

        """
        Compute barotropic components of local wave activity and flux terms in eqs.(2) and (3) in Nakamura and Huang (Science, 2018).

        Parameters
        ----------
        northern_hemisphere_results_only : bool
               If true, arrays of size [kmax, nlat//2] will be returned. Otherwise, arrays of size [kmax, nlat] will be returned. This parameter is present since the current version (v0.3.1) of the package only return analysis in the northern hemisphere. Default: True.


        Returns
        -------

        adv_flux_f1 : numpy.ndarray
            Two-dimensional array of the second-order eddy term in zonal advective flux,
            i.e. F1 in equation 3 of NH18, with dimension = [nlat//2+1, nlon] if northern_hemisphere_results_only=True, or dimension = [nlat, nlon] if northern_hemisphere_results_only=False.

        adv_flux_f2 : numpy.ndarray
            Two-dimensional array of the third-order eddy term in zonal advective flux,
            i.e. F2 in equation 3 of NH18, with dimension = [nlat//2+1, nlon] if northern_hemisphere_results_only=True, or dimension = [nlat, nlon] if northern_hemisphere_results_only=False.

        adv_flux_f3 : numpy.ndarray
            Two-dimensional array of the remaining term in zonal advective flux,
            i.e. F3 in equation 3 of NH18, with dimension = [nlat//2+1, nlon] if northern_hemisphere_results_only=True, or dimension = [nlat, nlon] if northern_hemisphere_results_only=False.

        convergence_zonal_advective_flux : numpy.ndarray
            Two-dimensional array of the convergence of zonal advective flux,
            i.e. -div(F1+F2+F3) in equation 3 of NH18, with dimension = [nlat//2+1, nlon] if northern_hemisphere_results_only=True, or dimension = [nlat, nlon] if northern_hemisphere_results_only=False.

        divergence_eddy_momentum_flux : numpy.ndarray
            Two-dimensional array of the divergence of eddy momentum flux,
            i.e. (II) in equation 2 of NH18, with dimension = [nlat//2+1, nlon] if northern_hemisphere_results_only=True, or dimension = [nlat, nlon] if northern_hemisphere_results_only=False.

        meridional_heat_flux : numpy.ndarray
            Two-dimensional array of the low-level meridional heat flux,
            i.e. (III) in equation 2 of NH18, with dimension = [nlat//2+1, nlon] if northern_hemisphere_results_only=True, or dimension = [nlat, nlon] if northern_hemisphere_results_only=False.

        lwa_baro : np.ndarray
            Two-dimensional array of barotropic local wave activity (with cosine weighting). Dimension = [nlat//2+1, nlon] if northern_hemisphere_results_only=True, or dimension = [nlat, nlon] if northern_hemisphere_results_only=False.

        u_baro     : np.ndarray
            Two-dimensional array of barotropic zonal wind (without cosine weighting). Dimension = [nlat//2+1, nlon] if northern_hemisphere_results_only=True, or dimension = [nlat, nlon] if northern_hemisphere_results_only=False.

        lwa : np.ndarray
            Three-dimensional array of barotropic local wave activity Dimension = [kmax, nlat//2+1, nlon] if northern_hemisphere_results_only=True, or dimension = [kmax, nlat, nlon] if northern_hemisphere_results_only=False.


        Examples
        --------

        >>> adv_flux_f1, adv_flux_f2, adv_flux_f3, convergence_zonal_advective_flux,
            divergence_eddy_momentum_flux, meridional_heat_flux,
            lwa_baro, u_baro, lwa = test_object.compute_lwa_and_barotropic_fluxes()

        """

        if self.qgpv_temp is None:
            self.interpolate_fields()

        if self.uref_temp is None:
            self.compute_reference_states()

        # === Compute barotropic flux terms ===
        lwa, astarbaro, ua1baro, ubaro, ua2baro,\
            ep1baro, ep2baro, ep3baro, ep4 = \
            compute_lwa_and_barotropic_fluxes(self.qgpv_temp,
                                              self.interpolated_u_temp,
                                              self.interpolated_v_temp,
                                              self.interpolated_theta_temp,
                                              self.qref_temp,
                                              self.uref_temp,
                                              self.ptref_temp,
                                              self.planet_radius,
                                              self.omega,
                                              self.dz,
                                              self.scale_height,
                                              self.dry_gas_constant,
                                              self.cp,
                                              self.prefactor)

        # === Compute divergence of the meridional eddy momentum flux ===
        meri_flux_temp = np.zeros_like(ep2baro)
        meri_flux_temp[:, 1:-1] = (ep2baro[:, 1:-1] - ep3baro[:, 1:-1]) / \
            (2 * self.planet_radius * self.dphi *
             np.cos(np.deg2rad(self.ylat[-self.equator_idx + 1:-1])))

        # === Compute convergence of the zonal LWA flux ===
        zonal_adv_flux_sum = np.swapaxes((ua1baro + ua2baro + ep1baro), 0, 1)
        convergence_zonal_advective_flux = \
            utilities.zonal_convergence(
                zonal_adv_flux_sum,
                np.cos(np.deg2rad(self.ylat[-self.equator_idx:])),
                self.dlambda,
                planet_radius=self.planet_radius
            )

        if northern_hemisphere_results_only:
            self.adv_flux_f1 = np.swapaxes(ua1baro, 0, 1)
            self.adv_flux_f2 = np.swapaxes(ua2baro, 0, 1)
            self.adv_flux_f3 = np.swapaxes(ep1baro, 0, 1)
            self.convergence_zonal_advective_flux = \
                convergence_zonal_advective_flux
            self.meridional_heat_flux = np.swapaxes(ep4, 0, 1)
            self.lwa_baro = np.swapaxes(astarbaro, 0, 1)
            self.u_baro = np.swapaxes(ubaro, 0, 1)
            self.lwa = np.swapaxes(lwa, 0, 2)
            self.divergence_eddy_momentum_flux = \
                np.swapaxes(meri_flux_temp, 0, 1)
            ylat_interp_from = self.ylat[-(self.adv_flux_f1.shape[0]):]
            if self.need_latitude_interpolation:
                ylat_interp_to = self.ylat_no_equator[
                    -(self.ylat_no_equator.size)//2:
                ]
        else:
            self.adv_flux_f1 = \
                np.vstack((np.zeros((self.equator_idx - 1, self.nlon)),
                           np.swapaxes(ua1baro, 0, 1)))

            self.adv_flux_f2 = np.vstack((np.zeros((self.equator_idx - 1,
                                                    self.nlon)),
                                          np.swapaxes(ua2baro, 0, 1)))

            self.adv_flux_f3 = np.vstack((np.zeros((self.equator_idx - 1,
                                                    self.nlon)),
                                          np.swapaxes(ep1baro, 0, 1)))

            self.convergence_zonal_advective_flux = \
                np.vstack(
                    (np.zeros((self.equator_idx - 1, self.nlon)),
                     np.swapaxes(convergence_zonal_advective_flux, 0, 1))
                )

            self.meridional_heat_flux = \
                np.vstack((np.zeros((self.equator_idx - 1, self.nlon)),
                           np.swapaxes(ep4, 0, 1)))

            self.lwa_baro = \
                np.vstack((np.zeros((self.equator_idx - 1, self.nlon)),
                           np.swapaxes(astarbaro, 0, 1)))

            self.u_baro = np.vstack((np.zeros((self.equator_idx - 1,
                                               self.nlon)),
                                     np.swapaxes(ubaro, 0, 1)))

            self.lwa = \
                np.concatenate((np.zeros((self.kmax, self.equator_idx - 1,
                                          self.nlon)),
                                np.swapaxes(lwa, 0, 2)), axis=1)

            self.divergence_eddy_momentum_flux = \
                np.vstack((np.zeros((self.equator_idx - 1, self.nlon)),
                           np.swapaxes(meri_flux_temp, 0, 1)))
            ylat_interp_from = self.ylat
            if self.need_latitude_interpolation:
                ylat_interp_to = self.ylat_no_equator

        if self.need_latitude_interpolation:

            self.adv_flux_f1 = self._interp_back(
                self.adv_flux_f1, ylat_interp_from, ylat_interp_to,
                which_axis=0
            )
            self.adv_flux_f2 = self._interp_back(
                self.adv_flux_f2, ylat_interp_from, ylat_interp_to,
                which_axis=0
            )
            self.adv_flux_f3 = self._interp_back(
                self.adv_flux_f3, ylat_interp_from, ylat_interp_to,
                which_axis=0
            )
            self.convergence_zonal_advective_flux = \
                self._interp_back(self.convergence_zonal_advective_flux,
                                  ylat_interp_from, ylat_interp_to,
                                  which_axis=0)
            self.divergence_eddy_momentum_flux = \
                self._interp_back(self.divergence_eddy_momentum_flux, 
                                  ylat_interp_from, ylat_interp_to,
                                  which_axis=0)
            self.meridional_heat_flux = \
                self._interp_back(self.meridional_heat_flux,
                                  ylat_interp_from, ylat_interp_to,
                                  which_axis=0)
            self.lwa_baro = self._interp_back(self.lwa_baro,
                                              ylat_interp_from,
                                              ylat_interp_to,
                                              which_axis=0)
            self.u_baro = self._interp_back(self.u_baro,
                                            ylat_interp_from,
                                            ylat_interp_to,
                                            which_axis=0)
            self.lwa = self._interp_back(self.lwa,
                                         ylat_interp_from,
                                         ylat_interp_to,
                                         which_axis=1)

        return self.adv_flux_f1, self.adv_flux_f2, self.adv_flux_f3, \
            self.convergence_zonal_advective_flux, \
            self.divergence_eddy_momentum_flux, \
            self.meridional_heat_flux, \
            self.lwa_baro, self.u_baro, self.lwa


def curl_2D(ufield, vfield, clat, dlambda, dphi, planet_radius=6.378e+6):
    """
    Assuming regular latitude and longitude [in degree] grid, compute the curl
    of velocity on a pressure level in spherical coordinates.
    """

    ans = np.zeros_like((ufield))
    ans[1:-1, 1:-1] = (vfield[1:-1, 2:] - vfield[1:-1, :-2])/(2.*dlambda) - \
                      (ufield[2:, 1:-1] * clat[2:, np.newaxis] -
                       ufield[:-2, 1:-1] * clat[:-2, np.newaxis])/(2.*dphi)
    ans[0, :] = 0.0
    ans[-1, :] = 0.0
    ans[1:-1, 0] = ((vfield[1:-1, 1] - vfield[1:-1, -1]) / (2. * dlambda) -
                    (ufield[2:, 0] * clat[2:] -
                     ufield[:-2, 0] * clat[:-2]) / (2. * dphi))
    ans[1:-1, -1] = ((vfield[1:-1, 0] - vfield[1:-1, -2]) / (2. * dlambda) -
                     (ufield[2:, -1] * clat[2:] -
                      ufield[:-2, -1] * clat[:-2]) / (2. * dphi))
    ans[1:-1, :] = ans[1:-1, :] / planet_radius / clat[1:-1, np.newaxis]
    return ans


class BarotropicField(object):

    """
    An object that deals with barotropic (2D) wind and/or PV fields

    Parameters
    ----------
        xlon : np.array
            Longitude array in degree with dimension = nlon.

        ylat : np.array
            Latitude array in degree with dimension = nlat.

        area : np.ndarray
            Differential area at each lon-lat grid points with dimension (nlat,nlon). If 'area=None': it will be initiated as area of uniform grid (in degree) on a spherical surface. Dimension = [nlat, nlon]

        dphi : np.array
            Differential length element along the lat grid with dimension = nlat.

        pv_field : np.ndarray
            Absolute vorticity field with dimension [nlat x nlon]. If 'pv_field=None': pv_field is expected to be computed with u,v,t field.


    Example
    ---------
    >>> barofield1 = BarotropicField(xlon, ylat, pv_field=abs_vorticity)

    """

    def __init__(self, xlon, ylat, pv_field, area=None, dphi=None,
                 n_partitions=None, planet_radius=6.378e+6):

        """Create a windtempfield object.

        Parameters
        ----------
            xlon : np.array
                Longitude array in degree with dimension = nlon.

            ylat : np.array
                Latitude array in degree with dimension = nlat.

            area : np.ndarray
                Differential area at each lon-lat grid points with dimension (nlat,nlon). If 'area=None': it will be initiated as area of uniform grid (in degree) on a spherical surface. Dimension = [nlat, nlon]

            dphi : np.array
                Differential length element along the lat grid with dimension = nlat.

            pv_field : np.ndarray
                Absolute vorticity field with dimension = [nlat, nlon].
                If none, pv_field is expected to be computed with u,v,t field.

            """

        self.xlon = xlon
        self.ylat = ylat
        self.clat = np.abs(np.cos(np.deg2rad(ylat)))
        self.nlon = xlon.size
        self.nlat = ylat.size
        self.planet_radius = planet_radius
        if dphi is None:
            self.dphi = pi/(self.nlat-1) * np.ones((self.nlat))
        else:
            self.dphi = dphi

        if area is None:
            self.area = 2.*pi*planet_radius**2*(np.cos(ylat[:, np.newaxis]*pi/180.)*self.dphi[:, np.newaxis])/float(self.nlon)*np.ones((self.nlat, self.nlon))
        else:
            self.area = area

        self.pv_field = pv_field

        if n_partitions is None:
            self.n_partitions = self.nlat
        else:
            self.n_partitions = n_partitions


    def equivalent_latitudes(self):

        """
        Compute equivalent latitude with the *pv_field* stored in the object.

        Return
        ----------
        An numpy array with dimension (nlat) of equivalent latitude array.

        Example
        ----------
        >>> barofield1 = BarotropicField(xlon, ylat, pv_field=abs_vorticity)
        >>> eqv_lat = barofield1.equivalent_latitudes()

        """

        from hn2016_falwa import basis

        self.eqvlat, dummy = basis.eqvlat(
            self.ylat, self.pv_field, self.area, self.n_partitions,
            planet_radius=self.planet_radius
        )

        return self.eqvlat

    def lwa(self):

        """

        Compute the finite-amplitude local wave activity based on the *equivalent_latitudes* and the *pv_field* stored in the object.

        Return
        ----------
        An 2-D numpy array with dimension [nlat,nlon] of local wave activity values.

        Example
        ----------
        >>> barofield1 = BarotropicField(xlon, ylat, pv_field=abs_vorticity)
        >>> eqv_lat = barofield1.equivalent_latitudes() # This line is optional
        >>> lwa = barofield1.lwa()

        """
        from hn2016_falwa import basis

        if self.eqvlat is None:
            self.eqvlat = self.equivalent_latitudes(self)

        lwa_ans, dummy = basis.lwa(
            self.nlon, self.nlat, self.pv_field, self.eqvlat,
            self.planet_radius * self.clat * self.dphi
        )
        return lwa_ans


if __name__ == "__main__":
    main()
