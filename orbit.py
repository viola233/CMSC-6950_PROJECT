# Standard library
import warnings

# Third-party
from astropy import log as logger
import astropy.coordinates as coord
import astropy.units as u
import numpy as np
from scipy.signal import argrelmax
from scipy.interpolate import InterpolatedUnivariateSpline
from scipy.optimize import minimize

# Project
from .core import PhaseSpacePosition
from .util import peak_to_peak_period
from .plot import plot_projections
from ..io import quantity_to_hdf5, quantity_from_hdf5
from ..util import atleast_2d
from ..units import dimensionless, UnitSystem, DimensionlessUnitSystem

__all__ = ['Orbit']


class Orbit(PhaseSpacePosition):
    """
    Represents an orbit: positions and velocities (conjugate momenta) as a
    function of time.

    The class can be instantiated with Astropy representation objects (e.g.,
    :class:`~astropy.coordinates.CartesianRepresentation`), Astropy
    :class:`~astropy.units.Quantity` objects, or plain Numpy arrays.

    If passing in Quantity or Numpy array instances for both position and
    velocity, they are assumed to be Cartesian. Array inputs are interpreted as
    dimensionless quantities. The input position and velocity objects can have
    an arbitrary number of (broadcastable) dimensions. For Quantity or array
    inputs, the first axes have special meaning:

        - ``axis=0`` is the coordinate dimension (e.g., x, y, z)
        - ``axis=1`` is the time dimension

    So if the input position array, ``pos``, has shape ``pos.shape = (3, 100)``,
    this would be a 3D orbit at 100 times (``pos[0]`` is ``x``, ``pos[1]``` is
    ``y``, etc.). For representing multiple orbits, the position array could
    have 3 axes, e.g., it might have shape `pos.shape = (3, 100, 8)`, where this
    is interpreted as a 3D position at 100 times for 8 different orbits. The
    same is true for velocity. The position and velocity arrays must have the
    same shape.

    If a time argument is specified, the position and velocity arrays must have
    the same number of timesteps as the length of the time object::

        len(t) == pos.shape[1]

    Parameters
    ----------
    pos : representation, quantity_like, or array_like
        Positions. If a numpy array (e.g., has no units), this will be
        stored as a dimensionless :class:`~astropy.units.Quantity`. See
        the note above about the assumed meaning of the axes of this object.
    vel : differential, quantity_like, or array_like
        Velocities. If a numpy array (e.g., has no units), this will be
        stored as a dimensionless :class:`~astropy.units.Quantity`. See
        the note above about the assumed meaning of the axes of this object.
    t : array_like, :class:`~astropy.units.Quantity` (optional)
        Array of times. If a numpy array (e.g., has no units), this will be
        stored as a dimensionless :class:`~astropy.units.Quantity`.
    hamiltonian : `~gala.potential.Hamiltonian` (optional)
        The Hamiltonian that the orbit was integrated in.

    """
    def __init__(self, pos, vel, t=None,
                 hamiltonian=None, potential=None, frame=None):

        super().__init__(pos=pos, vel=vel)

        if self.pos.ndim < 1:
            self.pos = self.pos.reshape(1)
            self.vel = self.vel.reshape(1)

        # TODO: check that Hamiltonian ndim is consistent with here

        if t is not None:
            t = np.atleast_1d(t)
            if self.pos.shape[0] != len(t):
                raise ValueError("Position and velocity must have the same "
                                 "length along axis=1 as the length of the "
                                 "time array {} vs {}"
                                 .format(len(t), self.pos.shape[0]))

            if not hasattr(t, 'unit'):
                t = t * u.one

        self.t = t

        if hamiltonian is not None:
            self.potential = hamiltonian.potential
            self.frame = hamiltonian.frame

        else:
            self.potential = potential
            self.frame = frame

    def __getitem__(self, slice_):

        if isinstance(slice_, np.ndarray) or isinstance(slice_, list):
            slice_ = (slice_,)

        try:
            slice_ = tuple(slice_)
        except TypeError:
            slice_ = (slice_,)

        kw = dict()
        if self.t is not None:
            kw['t'] = self.t[slice_[0]]

        pos = self.pos[slice_]
        vel = self.vel[slice_]

        # if one time is sliced out, return a phasespaceposition
        try:
            int_tslice = int(slice_[0])
        except TypeError:
            int_tslice = None

        if int_tslice is not None:
            return PhaseSpacePosition(pos=pos, vel=vel, frame=self.frame)

        else:
            return self.__class__(pos=pos, vel=vel,
                                  potential=self.potential,
                                  frame=self.frame, **kw)

    @property
    def hamiltonian(self):
        if self.potential is None or self.frame is None:
            return None

        try:
            return self._hamiltonian
        except AttributeError:
            from ..potential import Hamiltonian
            self._hamiltonian = Hamiltonian(potential=self.potential,
                                            frame=self.frame)

        return self._hamiltonian

    def w(self, units=None):
        """
        This returns a single array containing the phase-space positions.

        Parameters
        ----------
        units : `~gala.units.UnitSystem` (optional)
            The unit system to represent the position and velocity in
            before combining into the full array.

        Returns
        -------
        w : `~numpy.ndarray`
            A numpy array of all positions and velocities, without units.
            Will have shape ``(2*ndim, ...)``.

        """

        if units is None:
            if self.hamiltonian is None:
                units = dimensionless
            else:
                units = self.hamiltonian.units

        return super().w(units=units)

    # ------------------------------------------------------------------------
    # Convert from Cartesian to other representations
    #
    def represent_as(self, new_pos, new_vel=None):
        """
        Represent the position and velocity of the orbit in an alternate
        coordinate system. Supports any of the Astropy coordinates
        representation classes.

        Parameters
        ----------
        new_pos : :class:`~astropy.coordinates.BaseRepresentation`
            The type of representation to generate. Must be a class (not an
            instance), or the string name of the representation class.
        new_vel : :class:`~astropy.coordinates.BaseDifferential` (optional)
            Class in which any velocities should be represented. Must be a class
            (not an instance), or the string name of the differential class. If
            None, uses the default differential for the new position class.

        Returns
        -------
        new_orbit : `gala.dynamics.Orbit`
        """

        o = super().represent_as(new_pos=new_pos, new_vel=new_vel)
        return self.__class__(pos=o.pos,
                              vel=o.vel,
                              hamiltonian=self.hamiltonian)

    # ------------------------------------------------------------------------
    # Shape and size
    # ------------------------------------------------------------------------
    @property
    def ntimes(self):
        return self.shape[0]

    @property
    def norbits(self):
        if len(self.shape) < 2:
            return 1
        else:
            return self.shape[1]

    # ------------------------------------------------------------------------
    # Input / output
    #
    def to_hdf5(self, f):
        """
        Serialize this object to an HDF5 file.

        Requires ``h5py``.

        Parameters
        ----------
        f : str, :class:`h5py.File`
            Either the filename or an open HDF5 file.
        """

        f = super().to_hdf5(f)

        if self.potential is not None:
            import yaml
            from ..potential.potential.io import to_dict
            f['potential'] = yaml.dump(to_dict(self.potential)).encode('utf-8')

        if self.t:
            quantity_to_hdf5(f, 'time', self.t)

        return f

    @classmethod
    def from_hdf5(cls, f):
        """
        Load an object from an HDF5 file.

        Requires ``h5py``.

        Parameters
        ----------
        f : str, :class:`h5py.File`
            Either the filename or an open HDF5 file.
        """
        # TODO: this is duplicated code from PhaseSpacePosition
        if isinstance(f, str):
            import h5py
            f = h5py.File(f, mode='r')
            close = True
        else:
            close = False

        pos = quantity_from_hdf5(f['pos'])
        vel = quantity_from_hdf5(f['vel'])

        time = None
        if 'time' in f:
            time = quantity_from_hdf5(f['time'])

        frame = None
        if 'frame' in f:
            g = f['frame']

            frame_mod = g.attrs['module']
            frame_cls = g.attrs['class']
            frame_units = [u.Unit(x.decode('utf-8')) for x in g['units']]

            if u.dimensionless_unscaled in frame_units:
                units = DimensionlessUnitSystem()
            else:
                units = UnitSystem(*frame_units)

            pars = dict()
            for k in g['parameters']:
                pars[k] = quantity_from_hdf5(g['parameters/'+k])

            exec("from {0} import {1}".format(frame_mod, frame_cls))
            frame_cls = eval(frame_cls)

            frame = frame_cls(units=units, **pars)

        potential = None
        if 'potential' in f:
            import yaml
            from ..potential.potential.io import from_dict
            _dict = yaml.load(f['potential'][()].decode('utf-8'),
                              Loader=yaml.Loader)
            potential = from_dict(_dict)

        if close:
            f.close()

        return cls(pos=pos, vel=vel, t=time,
                   frame=frame, potential=potential)

    def orbit_gen(self):
        """
        Generator for iterating over each orbit.
        """
        if self.norbits == 1:
            yield self

        else:
            for i in range(self.norbits):
                yield self[:, i]

    # ------------------------------------------------------------------------
    # Computed dynamical quantities
    #

    def potential_energy(self, potential=None):
        r"""
        The potential energy *per unit mass*:

        .. math::

            E_\Phi = \Phi(\boldsymbol{q})

        Returns
        -------
        E : :class:`~astropy.units.Quantity`
            The potential energy.
        """
        if self.hamiltonian is None and potential is None:
            raise ValueError("To compute the potential energy, a potential"
                             " object must be provided!")
        if potential is None:
            potential = self.hamiltonian.potential

        return super().potential_energy(potential)

    def energy(self, hamiltonian=None):
        r"""
        The total energy *per unit mass*:

        Returns
        -------
        E : :class:`~astropy.units.Quantity`
            The total energy.
        """

        if self.hamiltonian is None and hamiltonian is None:
            raise ValueError("To compute the total energy, a hamiltonian"
                             " object must be provided!")

        from ..potential import PotentialBase
        if isinstance(hamiltonian, PotentialBase):
            from ..potential import Hamiltonian

            warnings.warn("This function now expects a `Hamiltonian` instance "
                          "instead of a `PotentialBase` subclass instance. If "
                          "you are using a static reference frame, you just "
                          "need to pass your potential object in to the "
                          "Hamiltonian constructor to use, e.g., Hamiltonian"
                          "(potential).", DeprecationWarning)

            hamiltonian = Hamiltonian(hamiltonian)

        if hamiltonian is None:
            hamiltonian = self.hamiltonian

        return hamiltonian(self)

    def _max_helper(self, arr, approximate=False,
                    interp_kwargs=None, minimize_kwargs=None):
        """
        Helper function for computing extrema (apocenter, pericenter, z_height)
        and times of extrema.

        Parameters
        ----------
        arr : `numpy.ndarray`
        """
        assert self.norbits == 1
        assert self.t[-1] > self.t[0]  # time must increase

        _ix = argrelmax(arr.value, mode='wrap')[0]
        _ix = _ix[(_ix != 0) & (_ix != (len(arr)-1))]  # remove edges
        t = self.t.value

        approx_arr = arr[_ix]
        approx_t = t[_ix]

        if approximate:
            return approx_arr, approx_t * self.t.unit

        if interp_kwargs is None:
            interp_kwargs = dict()

        if minimize_kwargs is None:
            minimize_kwargs = dict()

        # default scipy function kwargs
        interp_kwargs.setdefault('k', 3)
        interp_kwargs.setdefault('ext', 3)  # don't extrapolate, use boundary
        minimize_kwargs.setdefault('method', 'powell')

        # Interpolating function to upsample array:
        # Negative sign because we assume we're always finding the maxima
        interp_func = InterpolatedUnivariateSpline(t, -arr.value,
                                                   **interp_kwargs)

        better_times = np.zeros(_ix.shape, dtype=float)
        for i, j in enumerate(_ix):
            res = minimize(interp_func, t[j], **minimize_kwargs)
            better_times[i] = res.x

        better_arr = -interp_func(better_times)
        return better_arr * arr.unit, better_times * self.t.unit

    def _max_return_helper(self, vals, times, return_times, reduce):
        if return_times:
            if len(vals) == 1:
                return vals[0], times[0]
            else:
                return vals, times

        elif reduce:
            return u.Quantity(vals).reshape(self.shape[1:])

        else:
            return u.Quantity(vals)

    def pericenter(self, return_times=False, func=np.mean,
                   interp_kwargs=None, minimize_kwargs=None,
                   approximate=False):
        """
        Estimate the pericenter(s) of the orbit by identifying local minima in
        the spherical radius and interpolating between timesteps near the
        minima.

        By default, this returns the mean of all local minima (pericenters). To
        get, e.g., the minimum pericenter, pass in ``func=np.min``. To get
        all pericenters, pass in ``func=None``.

        Parameters
        ----------
        func : func (optional)
            A function to evaluate on all of the identified pericenter times.
        return_times : bool (optional)
            Also return the pericenter times.
        interp_kwargs : dict (optional)
            Keyword arguments to be passed to
            :class:`scipy.interpolate.InterpolatedUnivariateSpline`.
        minimize_kwargs : dict (optional)
            Keyword arguments to be passed to :class:`scipy.optimize.minimize`.
        approximate : bool (optional)
            Compute an approximate pericenter by skipping interpolation.

        Returns
        -------
        peri : float, :class:`~numpy.ndarray`
            Either a single number or an array of pericenters.
        times : :class:`~numpy.ndarray` (optional, see ``return_times``)
            If ``return_times=True``, also returns an array of the pericenter
            times.

        """

        if return_times and func is not None:
            raise ValueError("Cannot return times if reducing pericenters "
                             "using an input function. Pass `func=None` if "
                             "you want to return all individual pericenters "
                             "and times.")

        if func is None:
            reduce = False
            func = lambda x: x  # noqa
        else:
            reduce = True

        # time must increase
        if self.t[-1] < self.t[0]:
            self = self[::-1]

        vals = []
        times = []
        for orbit in self.orbit_gen():
            v, t = orbit._max_helper(-orbit.physicsspherical.r,  # pericenter
                                     interp_kwargs=interp_kwargs,
                                     minimize_kwargs=minimize_kwargs,
                                     approximate=approximate)
            vals.append(func(-v))  # negative for pericenter
            times.append(t)

        return self._max_return_helper(vals, times, return_times, reduce)

    def apocenter(self, return_times=False, func=np.mean,
                  interp_kwargs=None, minimize_kwargs=None,
                  approximate=False):
        """
        Estimate the apocenter(s) of the orbit by identifying local maxima in
        the spherical radius and interpolating between timesteps near the
        maxima.

        By default, this returns the mean of all local maxima (apocenters). To
        get, e.g., the largest apocenter, pass in ``func=np.max``. To get
        all apocenters, pass in ``func=None``.

        Parameters
        ----------
        func : func (optional)
            A function to evaluate on all of the identified apocenter times.
        return_times : bool (optional)
            Also return the apocenter times.
        interp_kwargs : dict (optional)
            Keyword arguments to be passed to
            :class:`scipy.interpolate.InterpolatedUnivariateSpline`.
        minimize_kwargs : dict (optional)
            Keyword arguments to be passed to :class:`scipy.optimize.minimize`.
        approximate : bool (optional)
            Compute an approximate apocenter by skipping interpolation.

        Returns
        -------
        apo : float, :class:`~numpy.ndarray`
            Either a single number or an array of apocenters.
        times : :class:`~numpy.ndarray` (optional, see ``return_times``)
            If ``return_times=True``, also returns an array of the apocenter
            times.

        """

        if return_times and func is not None:
            raise ValueError("Cannot return times if reducing apocenters "
                             "using an input function. Pass `func=None` if "
                             "you want to return all individual apocenters "
                             "and times.")

        if func is None:
            reduce = False
            func = lambda x: x  # noqa
        else:
            reduce = True

        # time must increase
        if self.t[-1] < self.t[0]:
            self = self[::-1]

        vals = []
        times = []
        for orbit in self.orbit_gen():
            v, t = orbit._max_helper(orbit.physicsspherical.r,  # apocenter
                                     interp_kwargs=interp_kwargs,
                                     minimize_kwargs=minimize_kwargs,
                                     approximate=approximate)
            vals.append(func(v))
            times.append(t)

        return self._max_return_helper(vals, times, return_times, reduce)

    def zmax(self, return_times=False, func=np.mean,
             interp_kwargs=None, minimize_kwargs=None,
             approximate=False):
        """
        Estimate the maximum ``z`` height of the orbit by identifying local
        maxima in the absolute value of the ``z`` position and interpolating
        between timesteps near the maxima.

        By default, this returns the mean of all local maxima. To get, e.g., the
        largest ``z`` excursion, pass in ``func=np.max``. To get all ``z``
        maxima, pass in ``func=None``.

        Parameters
        ----------
        func : func (optional)
            A function to evaluate on all of the identified z maximum times.
        return_times : bool (optional)
            Also return the times of maximum.
        interp_kwargs : dict (optional)
            Keyword arguments to be passed to
            :class:`scipy.interpolate.InterpolatedUnivariateSpline`.
        minimize_kwargs : dict (optional)
            Keyword arguments to be passed to :class:`scipy.optimize.minimize`.
        approximate : bool (optional)
            Compute approximate values by skipping interpolation.

        Returns
        -------
        zs : float, :class:`~numpy.ndarray`
            Either a single number or an array of maximum z heights.
        times : :class:`~numpy.ndarray` (optional, see ``return_times``)
            If ``return_times=True``, also returns an array of the apocenter
            times.

        """

        if return_times and func is not None:
            raise ValueError("Cannot return times if reducing "
                             "using an input function. Pass `func=None` if "
                             "you want to return all individual values "
                             "and times.")

        if func is None:
            reduce = False
            func = lambda x: x  # noqa
        else:
            reduce = True

        # time must increase
        if self.t[-1] < self.t[0]:
            self = self[::-1]

        vals = []
        times = []
        for orbit in self.orbit_gen():
            v, t = orbit._max_helper(np.abs(orbit.cylindrical.z),
                                     interp_kwargs=interp_kwargs,
                                     minimize_kwargs=minimize_kwargs,
                                     approximate=approximate)
            vals.append(func(v))
            times.append(t)

        return self._max_return_helper(vals, times, return_times, reduce)

    def eccentricity(self, **kw):
        r"""
        Returns the eccentricity computed from the mean apocenter and
        mean pericenter.

        .. math::

            e = \frac{r_{\rm apo} - r_{\rm per}}{r_{\rm apo} + r_{\rm per}}

        Parameters
        ----------
        **kw
            Any keyword arguments passed to ``apocenter()`` and
            ``pericenter()``. For example, ``approximate=True``.

        Returns
        -------
        ecc : float
            The orbital eccentricity.

        """
        ra = self.apocenter(**kw)
        rp = self.pericenter(**kw)
        return (ra - rp) / (ra + rp)

    def estimate_period(self, radial=True):
        """
        Estimate the period of the orbit. By default, computes the radial
        period. If ``radial==False``, this returns period estimates for
        each dimension of the orbit.

        Parameters
        ----------
        radial : bool (optional)
            What period to estimate. If ``True``, estimates the radial
            period. If ``False``, estimates period in each dimension, e.g.,
            if the orbit is 3D, along x, y, and z.

        Returns
        -------
        T : `~astropy.units.Quantity`
            The period or periods.
        """

        if self.t is None:
            raise ValueError("To compute the period, a time array is needed. "
                             "Specify a time array when creating this object.")

        if radial:
            r = self.physicsspherical.r.value
            if self.norbits == 1:
                T = u.Quantity(peak_to_peak_period(self.t, r))
            else:
                T = u.Quantity([peak_to_peak_period(self.t, r[:, n])
                                for n in range(r.shape[1])])

        else:
            raise NotImplementedError("sorry 'bout that...")

        return T

    # ------------------------------------------------------------------------
    # Misc. useful methods
    # ------------------------------------------------------------------------
    def circulation(self):
        """
        Determine which axes the Orbit circulates around by checking
        whether there is a change of sign of the angular momentum
        about an axis. Returns a 2D array with ``ndim`` integers per orbit
        point. If a box orbit, all integers will be 0. A 1 indicates
        circulation about the corresponding axis.

        TODO: clockwise / counterclockwise?

        For example, for a single 3D orbit:

        - Box and boxlet = [0, 0, 0]
        - z-axis (short-axis) tube = [0, 0, 1]
        - x-axis (long-axis) tube = [1, 0, 0]

        Returns
        -------
        circulation : :class:`numpy.ndarray`
            An array that specifies whether there is circulation about any of
            the axes of the input orbit. For a single orbit, will return a
            1D array, but for multiple orbits, the shape will be
            ``(3, norbits)``.

        """
        L = self.angular_momentum()

        # if only 2D, add another empty axis
        if L.ndim == 2:
            single_orbit = True
            L = L[..., None]
        else:
            single_orbit = False

        ndim, ntimes, norbits = L.shape

        # initial angular momentum
        L0 = L[:, 0]

        # see if at any timestep the sign has changed
        circ = np.ones((ndim, norbits))
        for ii in range(ndim):
            cnd = (np.sign(L0[ii]) != np.sign(L[ii, 1:])) | \
                  (np.abs(L[ii, 1:]).value < 1E-13)
            ix = np.atleast_1d(np.any(cnd, axis=0))
            circ[ii, ix] = 0

        circ = circ.astype(int)
        if single_orbit:
            return circ.reshape((ndim,))
        else:
            return circ

    def align_circulation_with_z(self, circulation=None):
        """
        If the input orbit is a tube orbit, this function aligns the circulation
        axis with the z axis and returns a copy.

        Parameters
        ----------
        circulation : array_like (optional)
            Array of bits that specify the axis about which the orbit
            circulates. If not provided, will compute this using
            :meth:`~gala.dynamics.Orbit.circulation`. See that method for more
            information.

        Returns
        -------
        orb : :class:`~gala.dynamics.Orbit`
            A copy of the original orbit object with circulation aligned with
            the z axis.
        """

        if circulation is None:
            circulation = self.circulation()
        circulation = atleast_2d(circulation, insert_axis=1)

        cart = self.cartesian
        pos = cart.xyz
        vel = np.vstack((cart.v_x.value[None],
                         cart.v_y.value[None],
                         cart.v_z.value[None])) * cart.v_x.unit

        if pos.ndim < 3:
            pos = pos[..., np.newaxis]
            vel = vel[..., np.newaxis]

        if (circulation.shape[0] != self.ndim or
                circulation.shape[1] != pos.shape[2]):
            raise ValueError("Shape of 'circulation' array should match the "
                             "shape of the position/velocity (minus the time "
                             "axis).")

        new_pos = pos.copy()
        new_vel = vel.copy()
        for n in range(pos.shape[2]):
            if circulation[2, n] == 1 or np.all(circulation[:, n] == 0):
                # already circulating about z or box orbit
                continue

            if sum(circulation[:, n]) > 1:
                logger.warning("Circulation about multiple axes - are you sure "
                               "the orbit has been integrated for long enough?")

            if circulation[0, n] == 1:
                circ = 0
            elif circulation[1, n] == 1:
                circ = 1
            else:
                raise RuntimeError("Should never get here...")

            new_pos[circ, :, n] = pos[2, :, n]
            new_pos[2, :, n] = pos[circ, :, n]

            new_vel[circ, :, n] = vel[2, :, n]
            new_vel[2, :, n] = vel[circ, :, n]

        return self.__class__(pos=new_pos.reshape(cart.xyz.shape),
                              vel=new_vel.reshape(cart.xyz.shape),
                              t=self.t,
                              hamiltonian=self.hamiltonian)

    def plot(self, components=None, units=None, auto_aspect=True, **kwargs):
        """
        Plot the positions in all projections. This is a wrapper around
        `~gala.dynamics.plot_projections` for fast access and quick
        visualization. All extra keyword arguments are passed to that function
        (the docstring for this function is included here for convenience).

        Parameters
        ----------
        components : iterable (optional)
            A list of component names (strings) to plot. By default, this is the
            Cartesian positions ``['x', 'y', 'z']``. To plot Cartesian
            velocities, pass in the velocity component names
            ``['v_x', 'v_y', 'v_z']``. If the representation is different, the
            component names will be different. For example, for a Cylindrical
            representation, the components are ``['rho', 'phi', 'z']`` and
            ``['v_rho', 'pm_phi', 'v_z']``.
        units : `~astropy.units.UnitBase`, iterable (optional)
            A single unit or list of units to display the components in.
        auto_aspect : bool (optional)
            Automatically enforce an equal aspect ratio.
        relative_to : bool (optional)
            Plot the values relative to this value or values.
        autolim : bool (optional)
            Automatically set the plot limits to be something sensible.
        axes : array_like (optional)
            Array of matplotlib Axes objects.
        subplots_kwargs : dict (optional)
            Dictionary of kwargs passed to :func:`~matplotlib.pyplot.subplots`.
        labels : iterable (optional)
            List or iterable of axis labels as strings. They should correspond to
            the dimensions of the input orbit.
        plot_function : callable (optional)
            The ``matplotlib`` plot function to use. By default, this is
            :func:`~matplotlib.pyplot.scatter`, but can also be, e.g.,
            :func:`~matplotlib.pyplot.plot`.
        **kwargs
            All other keyword arguments are passed to the ``plot_function``.
            You can pass in any of the usual style kwargs like ``color=...``,
            ``marker=...``, etc.

        Returns
        -------
        fig : `~matplotlib.Figure`

        """

        try:
            import matplotlib.pyplot as plt
        except ImportError:
            msg = 'matplotlib is required for visualization.'
            raise ImportError(msg)

        if components is None:
            if self.ndim == 1:  # only a 1D orbit, so just plot time series
                components = ['t', self.pos.components[0]]
            else:
                components = self.pos.components

        x, labels = self._plot_prepare(components=components,
                                       units=units)

        default_kwargs = {
            'marker': '',
            'linestyle': '-',
            'labels': labels,
            'plot_function': plt.plot
        }

        for k, v in default_kwargs.items():
            kwargs[k] = kwargs.get(k, v)

        fig = plot_projections(x, **kwargs)

        if self.pos.get_name() == 'cartesian' and \
                all([not c.startswith('d_') for c in components]) and \
                't' not in components and \
                auto_aspect:
            for ax in fig.axes:
                ax.set(aspect='equal', adjustable='datalim')

        return fig

    def to_frame(self, frame, current_frame=None, **kwargs):
        """
        TODO:

        Parameters
        ----------
        frame : `gala.potential.CFrameBase`
            The frame to transform to.
        current_frame : `gala.potential.CFrameBase` (optional)
            If the Orbit has no associated Hamiltonian, this specifies the
            current frame of the orbit.

        Returns
        -------
        orbit : `gala.dynamics.Orbit`
            The orbit in the new reference frame.

        """

        kw = kwargs.copy()

        # TODO: this short-circuit sux
        if current_frame is None:
            current_frame = self.frame
        if frame == current_frame and not kwargs:
            return self

        # TODO: need a better way to do this!
        from ..potential.frame.builtin import ConstantRotatingFrame
        for fr in [frame, current_frame, self.frame]:
            if isinstance(fr, ConstantRotatingFrame):
                if 't' not in kw:
                    kw['t'] = self.t

        # TODO: this needs a re-write...
        psp = super().to_frame(frame, current_frame, **kw)

        return Orbit(pos=psp.pos, vel=psp.vel, t=self.t,
                     frame=frame, potential=self.potential)

    # ------------------------------------------------------------------------
    # Compatibility with other packages
    #

    def to_galpy_orbit(self, ro=None, vo=None):
        """Convert this object to a ``galpy.Orbit`` instance.

        Parameters
        ----------
        ro : `astropy.units.Quantity` or `astropy.units.UnitBase`
            "Natural" length unit.
        vo : `astropy.units.Quantity` or `astropy.units.UnitBase`
            "Natural" velocity unit.

        Returns
        -------
        galpy_orbit : `galpy.orbit.Orbit`

        """
        from galpy.orbit import Orbit
        from galpy.util.config import __config__ as galpy_config

        if self.frame is not None:
            from ..potential import StaticFrame
            w = self.to_frame(StaticFrame(self.frame.units))
        else:
            w = self

        if ro is None:
            ro = galpy_config.getfloat('normalization', 'ro')
            ro = ro * u.kpc

        if vo is None:
            vo = galpy_config.getfloat('normalization', 'vo')
            vo = vo * u.km/u.s

        # PhaseSpacePosition or Orbit:
        cyl = w.cylindrical

        R = cyl.rho.to_value(ro).T
        phi = cyl.phi.to_value(u.rad).T
        z = cyl.z.to_value(ro).T

        vR = cyl.v_rho.to_value(vo).T
        vT = (cyl.rho * cyl.pm_phi).to_value(vo, u.dimensionless_angles()).T
        vz = cyl.v_z.to_value(vo).T

        o = Orbit(np.array([R, vR, vT, z, vz, phi]).T, ro=ro, vo=vo)
        if w.t is not None:
            o.t = w.t.to_value(ro / vo)

        return o

    @classmethod
    def from_galpy_orbit(self, galpy_orbit):
        """Create a Gala ``PhaseSpacePosition`` or ``Orbit`` instance from a
        ``galpy.Orbit`` instance.

        Parameters
        ----------
        galpy_orbit : :class:`galpy.orbit.Orbit`

        Returns
        -------
        orbit : :class:`~gala.dynamics.Orbit`

        """
        ro = galpy_orbit._ro * u.kpc
        vo = galpy_orbit._vo * u.km/u.s
        ts = galpy_orbit.t

        rep = coord.CylindricalRepresentation(
            rho=galpy_orbit.R(ts) * ro,
            phi=galpy_orbit.phi(ts) * u.rad,
            z=galpy_orbit.z(ts) * ro
        )
        with u.set_enabled_equivalencies(u.dimensionless_angles()):
            dif = coord.CylindricalDifferential(
                d_rho=galpy_orbit.vR(ts) * vo,
                d_phi=galpy_orbit.vT(ts) * vo / rep.rho,
                d_z=galpy_orbit.vz(ts) * vo
            )

        t = galpy_orbit.t * ro / vo
        return Orbit(rep, dif, t=t)
