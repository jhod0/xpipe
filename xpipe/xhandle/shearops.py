"""
Postprocess XSHEAR output
"""


import math

import numpy as np

from .ioshear import read_single_bin, read_multiple_bin
from .xwrap import sheared_tags

BADVAL = -9999.0


def _get_nbin(data):
    """obtains number of radial bins from **xread** output"""
    return len(data[0, 0, :])


def redges(rmin, rmax, nbin):
    """
    Calculates nominal edges and centers for logarithmic radial bins(base10 logarithm)

    Edges and areas are exact, "center" values are estimated as CIRCUMFERENCE weighted
    mean radius

    Parameters
    ----------
    rmin : float
        inner edge
    rmax : float
        outer edge
    nbin : int
        number of bins

    Returns
    -------
    np.array, np.array, np.array
        centers, edges, areas

    """
    edges = np.logspace(math.log10(rmin), math.log10(rmax), nbin + 1,
                        endpoint=True)
    cens = np.array([(edges[i + 1] ** 3. - edges[i] ** 3.) * 2. / 3. /
                     (edges[i + 1] ** 2. - edges[i] ** 2.)
                     for i, edge in enumerate(edges[:-1])])

    areas = np.array([np.pi * (edges[i + 1] ** 2. - edges[i] ** 2.)
                      for i, val in enumerate(edges[:-1])])

    return cens, edges, areas


class StackedProfileContainer(object):
    def __init__(self, info, data, labels, ncen, lcname=None, metadata=None, metatags=None, **kwargs):
        """
        Object Oriented interface for stacked shear and :math:`\\Delta\\Sigma` calculated via **xshear**

        **Features**

            * Jackknife based covariance estimation

            * Calculate *shear*, *DeltaSigma*, and other averaged quantities

            * Simple arithmetic with shear profiles: :code:`+, -, *, /`

            * Integration with *Metacalibration* style selection responses


        **Overview**

        The lensing profile is calculated in two stages:

            * First, for each Jackknife-region defined by the :code:`labels`
              (that is for all-except-one region) the lensing profile is calculated, and stored in
              :code:`self.*_sub` (These are the JK-subprofiles).

            * Using these JK-subprofiles, the mean lensing profile and the corresponding covariance
              is calculated via blockwise omitt-one Jackknife resampling.

        In practice both of these steps are performed when calling **self.prof_maker()**, and the results are
        stored in the below varaiables. The default quantity calculated is the **DeltaSigma**::

            self.rr:        radial centers
            self.dst:       E-mode lensing signal
            self.dst_err:   error for E-mode lensing signal
            self.dst_cov:   covariance for E-mode lensing signal
            self.dst:       B-mode lensing signal
            self.dst_err:   error for B-mode lensing signal
            self.dst_cov:   covariance for B-mode  lensing signal

        A benefit of this two-step method, is that it allows for the quick calculation of errors on the
        **differences**, **ratios** and other transformations of shear profiles.

        In particular, the **self.composite** function allows four basic operations :code:`+, -, *, /`
        between shear profiles, while the **self.multiply** function allows :code:`\*, /`
        with integers or 1-D arrays (in which case each element is a value for a lens)

            E.g to calculate :math:`\\Delta\\Sigma_3 = \\Delta\\Sigma_2 - \\Delta\\Sigma_1`,
            we only need to compute the differences of JK-subprofiles and
            then re-compute the JK-mean, and JK-covariance.

            In practice the above operation is achieved by::

                # the operation is performed in-place, hence the deepcopy
                prof3 = copy.deepcopy(prof2)
                prof3.composite(prof1, operation="-")

        Changing the physical quantity computed by **self.prof_maker()** is possibly by modifying the
        columns used by the stacking algorithm. The default values used for **DeltaSigma** are::

            # default values for stacked DeltaSigma
            self.dst_nom = 4
            self.dsx_nom = 5
            self.dst_denom = 6
            self.dsx_denom = 7

            self.e1_nom = 10
            self.e2_nom = 11
            self.meta_denom = 3
            self.meta_prefac = 2

        To calculate **shear** one would need to change the above to::

            self.dst_denom = 8
            self.dsx_denom = 9

            self.meta_denom = 3
            self.meta_prefac = 3

        Further information on the column positions are found in the **xread** documentation...


        Parameters
        ----------
        info : np.ndarray
            :code:`info` table from **xread**
        data : np.ndarray
            :code:`data` table from **xread**
        labels : np.array
            JK-region labels
        ncen : int
            number of JK regions in total
        lcname : str
            tag string
        metadata : list of np.ndarray
            sheared versions of :code:`data`
        metatags : list of str
            sheared tags
        """

        # indices to be used in the shear stacking
        self.dst_nom = 4
        self.dsx_nom = 5
        self.dst_denom = 6
        self.dsx_denom = 7

        self.e1_nom = 10
        self.e2_nom = 11
        self.meta_denom = 3
        self.meta_prefac = 2

        self.snum_ind = 2

        # input params saved
        self.info = info
        self.data = data
        self.lcname = lcname

        self.nbin = 1
        if self.data is not None:
            self.nbin = _get_nbin(data)

        self.num = 1
        if self.info is not None:
            self.num = len(info)

        # Data needed for metacalibration corrections
        self.metadata = metadata
        self.metatags = metatags
        self.ismeta = self.metadata is not None and self.metatags is not None

        self.labels = labels
        self.ncen = ncen  # number of centers

        # containers for stacking parameters
        self.weights = None  # stacking weights

        # containers for the Jackknife sampling
        self.sub_labels = None
        self.subcounts = None
        self.indexes = None
        self.non_indexes = None
        self.hasval = None
        self.wdata = None

        self.dsx_sub = None
        self.dst_sub = None
        self.snum_sub = None

        # containers for the resulting profile
        self.w = np.ones(self.num)
        self.rr = np.ones(self.nbin) * BADVAL
        self.dst0 = np.zeros(self.nbin)
        self.dsx0 = np.zeros(self.nbin)
        self.dst = np.zeros(self.nbin)
        self.dsx = np.zeros(self.nbin)
        self.dst_cov = np.zeros((self.nbin, self.nbin))
        self.dst_err = np.zeros(self.nbin)
        self.dsx_cov = np.zeros((self.nbin, self.nbin))
        self.dsx_err = np.zeros(self.nbin)

        # containers for the source number profile
        self.snum = np.zeros(self.nbin)
        self.snum0 = np.zeros(self.nbin)
        self.snum_err = np.zeros(self.nbin)
        self.snum_cov = np.zeros((self.nbin, self.nbin))

        self.neff = 0  # number of entries with sources in any bin
        self.hasprofile = False
        self.hasdata = True

    @classmethod
    def from_sub_dict(cls, sub_dict):
        """
        Reconstructs the object from a dictionary

        The resulting object does not contain the base data, only the profile, JK-subprofiles and
        ancillary quantities
        """

        spc = cls(None, None, None, sub_dict["ncen"], lcname=sub_dict["lcname"])
        for key in sub_dict:
            setattr(spc, key, sub_dict[key])
        spc.hasdata = False
        return spc

    def to_sub_dict(self):
        """
        Extracts the calculated profile and ancillary information to a dictionary

        The resulting dict does not contain the base data, only the profile, the JK-subprofiles and
        ancillary quantities
        """

        infodict = {
            "lcname": self.lcname,
            "nbin": self.nbin,
            "num": self.num,
            "ismeta": self.ismeta,
            "ncen": self.ncen,
            "sub_labels": self.sub_labels,
            "subcounts": self.subcounts,
            "indexes": self.indexes,
            "non_indexes": self.non_indexes,
            "hasval": self.hasval,
            "wdata": self.wdata,
            "dsx_sub": self.dsx_sub,
            "dst_sub": self.dst_sub,
            "snum_sub": self.snum_sub,
            "rr": self.rr,
            "dst0": self.dst0,
            "dsx0": self.dsx0,
            "dst": self.dst,
            "dsx": self.dsx,
            "dst_cov": self.dst_cov,
            "dst_err": self.dst_err,
            "dsx_cov": self.dsx_cov,
            "dsx_err": self.dsx_err,
            "snum": self.snum,
            "snum0": self.snum0,
            "snum_err": self.snum_err,
            "snum_cov": self.snum_cov,
            "neff": self.neff,
            "hasprofile": self.hasprofile,
        }
        return infodict

    def drop_data(self):
        """
        resets the :code:`info`, :code:`data` and :code:`labels` containers to :code:`None`

        This reduces the size of the object in memory, but cannot re-stack the lenses anymore.
        Leaves the profile, JK-subprofiles and ancillary quantities unaffected
        """
        self.info = None
        self.data = None
        self.labels = None
        self.hasdata = False

    def _reset_profile(self, keep_rr=True):
        """Resets the profile container, while keeping the base data and JK-subprofiles"""
        self.w = np.ones(self.num)
        if not keep_rr:
            self.rr = np.ones(self.nbin) * -1.0
        self.dst0 = np.zeros(self.nbin)
        self.dsx0 = np.zeros(self.nbin)
        self.dst = np.zeros(self.nbin)
        self.dsx = np.zeros(self.nbin)
        self.dst_cov = np.zeros((self.nbin, self.nbin))
        self.dst_err = np.zeros(self.nbin)
        self.dsx_cov = np.zeros((self.nbin, self.nbin))
        self.dsx_err = np.zeros(self.nbin)

        self.hasprofile = False

    def _setup_subpatches(self):
        """Quick setup for JK-subpatches"""

        self.sub_labels = np.unique(self.labels).astype(int)

        # indexes of clusters for subsample i
        self.indexes = [np.where(self.labels != ind)[0]
                        for ind in self.sub_labels]

        # indexes of clusters not in subsample i
        self.non_indexes = [np.where(self.labels == ind)[0]
                            for ind in self.sub_labels]

        self.dsx_sub = np.zeros(shape=(self.nbin, self.ncen))
        self.dst_sub = np.zeros(shape=(self.nbin, self.ncen))
        self.snum_sub = np.zeros(shape=(self.nbin, self.ncen))

    def _get_rr(self):
        """calculating radial values for data points"""
        nzind = np.where(np.sum(self.data[0, :, :], axis=0) > 0)[0]

        self.rr[nzind] = np.sum(self.data[1, :, nzind] * self.w, axis=1) / \
                         np.sum(self.data[2, :, nzind] * self.w, axis=1)

    def _get_neff(self):
        """calculates effective number of entries (lenses)"""
        return len(np.nonzero(self.info[:, 2])[0])


    def _get_single_subcounts(self, data):
        subcounts = np.array([np.sum(data[0, ind, :], axis=0)
                              for ind in self.indexes]).astype(int)
        return subcounts

    def _get_subcounts(self):
        tmp_subcounts = []

        # processing main data
        subcount = self._get_single_subcounts(self.data)
        tmp_subcounts.append(subcount)

        if self.ismeta:
            for i, tag in enumerate(self.metatags):
                subcount = self._get_single_subcounts(self.metadata[i])
                tmp_subcounts.append(subcount)
        tmp_subcounts = np.dstack(tmp_subcounts)
        subcounts = np.min(tmp_subcounts, axis=2)
        return subcounts

    def _subprofiles(self):
        """Calculates subprofiles for each patch"""

        self.subcounts = self._get_subcounts()
        hasval = [np.nonzero(arr.astype(bool))[0] for arr in self.subcounts]

        # calculating jackknife subprofiles
        for i, lab in enumerate(self.sub_labels):
            # print i, lab
            ind = self.indexes[i]
            cind = hasval[i]

            ww = self.w[ind, np.newaxis]
            wsum = np.sum(self.w[ind])

            # TODO log responses

            Rs = np.zeros(len(cind))
            if self.ismeta:
                val1parr = (np.sum(self.metadata[0][self.e1_nom, ind][:, cind] * ww, axis=0) /
                            np.sum(self.metadata[0][self.meta_denom, ind][:, cind] * ww, axis=0))
                val1marr = (np.sum(self.metadata[1][self.e1_nom, ind][:, cind] * ww, axis=0) /
                            np.sum(self.metadata[1][self.meta_denom, ind][:, cind] * ww, axis=0))
                R11 = (val1parr - val1marr) / 0.02

                val2parr = (np.sum(self.metadata[2][self.e2_nom, ind][:, cind] * ww, axis=0) /
                            np.sum(self.metadata[2][self.meta_denom, ind][:, cind] * ww, axis=0))
                val2marr = (np.sum(self.metadata[3][self.e2_nom, ind][:, cind] * ww, axis=0) /
                            np.sum(self.metadata[3][self.meta_denom, ind][:, cind] * ww, axis=0))
                R22 = (val2parr - val2marr) / 0.02
                Rs = 0.5 * (R11 + R22) * np.sum(self.data[self.meta_prefac, ind][:, cind] * ww, axis=0)

            dsum_jack = np.sum(self.data[self.dst_nom, ind][:, cind] * ww, axis=0)
            dsum_w_jack = np.sum(self.data[self.dst_denom, ind][:, cind] * ww, axis=0)

            self.dst_sub[cind, lab] = dsum_jack / (dsum_w_jack + Rs)

            osum_jack = np.sum(self.data[self.dsx_nom, ind][:, cind] * ww, axis=0)
            osum_w_jack = np.sum(self.data[self.dsx_denom, ind][:, cind] * ww, axis=0)
            self.dsx_sub[cind, lab] = osum_jack / (osum_w_jack + Rs)

            self.snum_sub[cind, lab] = np.sum(self.data[self.snum_ind, ind][:, cind] * ww, axis=0) / wsum
            self.snum_sub[:, lab] /= np.sum(self.snum_sub[:, lab])

    def _profcalc(self):
        """JK estimate on the mean profile"""

        tmp_rr = self.rr
        self.rr = np.ones(len(self.rr)) * BADVAL

        for r in range(self.nbin):
            # checking for radial bins with 1 pair count (to avoid NaNs)
            subind = self.sub_labels[np.where(self.subcounts[:, r] > 1)[0]]
            njk = len(subind)
            if njk > 1:
                self.dst[r] = np.sum(self.dst_sub[r, subind]) / njk
                self.dsx[r] = np.sum(self.dsx_sub[r, subind]) / njk
                self.snum[r] = np.sum(self.snum_sub[r, subind]) / njk
                self.rr[r] = tmp_rr[r]

    def _covcalc(self):
        """JK estimate on the covariance matrix"""
        # calculating the covariance
        for r1 in range(self.nbin):
            for r2 in range(self.nbin):
                # getting patches where there are multiple elements in both indices
                subind1 = self.sub_labels[np.where(
                    self.subcounts[:, r1] > 1)[0]]
                subind2 = self.sub_labels[np.where(
                    self.subcounts[:, r2] > 1)[0]]
                # overlapping indices
                subind = list(set(subind1).intersection(set(subind2)))
                njk = len(subind)
                if njk > 1:
                    self.dst_cov[r1, r2] = np.sum((self.dst_sub[r1, subind] -
                                                   self.dst[r1]) *
                                                  (self.dst_sub[r2, subind] -
                                                   self.dst[r2])) * \
                                           (njk - 1.0) / njk

                    self.dsx_cov[r1, r2] = np.sum((self.dsx_sub[r1, subind] -
                                                   self.dsx[r1]) *
                                                  (self.dsx_sub[r2, subind] -
                                                   self.dsx[r2])) * \
                                           (njk - 1.0) / njk

                    self.snum_cov[r1, r2] = np.sum((self.snum_sub[r1, subind] -
                                                   self.snum[r1]) *
                                                  (self.snum_sub[r2, subind] -
                                                   self.snum[r2])) * \
                                           (njk - 1.0) / njk

        self.dst_err = np.sqrt(np.diag(self.dst_cov))
        self.dsx_err = np.sqrt(np.diag(self.dsx_cov))
        self.snum_err = np.sqrt(np.diag(self.snum_cov))

    def prof_maker(self, weights=None):
        """
        Calculates the Jackknife estimate of on stacked profile and on the corresponding covariance

        Parameters
        ----------
        weights : np.array
            weight for each entry in the lens catalog
        """

        # adding weights
        if weights is None:
            weights = np.ones(self.num)

        # print weights.shape
        self.w = weights

        # preparing the JK patches
        self._setup_subpatches()

        # getting radius values
        self._get_rr()

        # calculating the profiles
        self._subprofiles()
        self._profcalc()
        self._covcalc()

        self.neff = self._get_neff()
        self.hasprofile = True

    def _composite_subprofiles(self, other, operation="-"):
        """Applies the binary operation to the profile objects"""

        tmp_dst_sub = np.zeros(shape=(self.nbin, self.ncen))
        tmp_dsx_sub = np.zeros(shape=(self.nbin, self.ncen))
        tmp_snum_sub = np.zeros(shape=(self.nbin, self.ncen))

        tmp_sub_labels = np.array(
            list(set(self.sub_labels).intersection(set(other.sub_labels))))
        tmp_subcounts = np.zeros((len(tmp_sub_labels), self.nbin))
        # print(self.subcounts)
        for i in range(len(tmp_sub_labels)):
            ind = tmp_sub_labels[i]
            ind1 = np.where(self.sub_labels == ind)[0][0]
            ind2 = np.where(other.sub_labels == ind)[0][0]
            for j in range(self.nbin):
                tmp_subcounts[i, j] = np.min(
                    (self.subcounts[ind1, j], other.subcounts[ind2, j]))

        for r in range(self.nbin):
            subind = tmp_sub_labels[np.where(tmp_subcounts[:, r] > 0)[0]]
            if np.max(tmp_subcounts[:, r]) == 1:
                self.rr[r] = -1

            njk = len(subind)
            if njk > 1:
                if operation == "-":
                    tmp_dst_sub[r, subind] = self.dst_sub[r, subind] - \
                                             other.dst_sub[r, subind]
                    tmp_dsx_sub[r, subind] = self.dsx_sub[r, subind] - \
                                             other.dsx_sub[r, subind]
                    tmp_snum_sub[r, subind] = self.snum_sub[r, subind] - \
                                             other.snum_sub[r, subind]
                elif operation == "+":
                    tmp_dst_sub[r, subind] = self.dst_sub[r, subind] + \
                                             other.dst_sub[r, subind]
                    tmp_dsx_sub[r, subind] = self.dsx_sub[r, subind] + \
                                             other.dsx_sub[r, subind]
                    tmp_snum_sub[r, subind] = self.snum_sub[r, subind] + \
                                             other.snum_sub[r, subind]
                elif operation == "*":
                    tmp_dst_sub[r, subind] = self.dst_sub[r, subind] * \
                                             other.dst_sub[r, subind]
                    tmp_dsx_sub[r, subind] = self.dsx_sub[r, subind] * \
                                             other.dsx_sub[r, subind]
                    tmp_snum_sub[r, subind] = self.snum_sub[r, subind] * \
                                             other.snum_sub[r, subind]
                elif operation == "/":
                    tmp_dst_sub[r, subind] = self.dst_sub[r, subind] / \
                                             other.dst_sub[r, subind]
                    tmp_dsx_sub[r, subind] = self.dsx_sub[r, subind] / \
                                             other.dsx_sub[r, subind]
                    tmp_snum_sub[r, subind] = self.snum_sub[r, subind] / \
                                             other.snum_sub[r, subind]

                else:
                    raise ValueError("Operation not supported, use ('+', '-', '*', '/')")

        # assigning updated containers
        self.sub_labels = tmp_sub_labels
        self.subcounts = tmp_subcounts
        self.dst_sub = tmp_dst_sub
        self.dsx_sub = tmp_dsx_sub
        self.snum_sub = tmp_snum_sub


    def multiply(self, val, keep_rr=True):
        """
        Calculate the JK estimate on profile times the specified "val",

        Operation performed in-place.
        Use deepcopy to obtain copies of the object for storing the previous state.

        Parameters
        ----------
        val : float or np.array
            value to multiply by
        """

        # clears the profile container
        self._reset_profile(keep_rr=keep_rr)

        if not keep_rr:
            # getting radius values
            self._get_rr()

        # performs the multiplication
        self.dst_sub *= val

        # re calculates profiles
        self._profcalc()
        self._covcalc()

        self.hasprofile = True


    def composite(self, other, operation="-", keep_rr=True):
        """
        Calculate the JK estimate on the operation applied to the two profiles

        **Possible Operations:**

            * "-": self - other

            * "+": self + other

            * "*": self * other

            * "/": self / other

        The results is updated to self. Use deepcopy to obtain
        copies of the object for storing the previous state.


        Parameters
        ----------
        other : StackedProfileContainer
            an other profile...
        operation : str
            string specifying what to do...
        keep_rr : bool
            whether to reset radial values to placeholder
        """

        # making sure that there is a profile in both containers
        assert self.hasprofile and other.hasprofile

        # making sure that the two profiles use the same centers
        # err_msg = 'JK centers do not agree within 1e-5'
        # np.testing.assert_allclose(self.centers, other.centers,
        #                            rtol=1e-5, err_msg=err_msg)
        assert self.dst_sub.shape == other.dst_sub.shape

        # clears the profile container
        self._reset_profile(keep_rr=keep_rr)

        if not keep_rr:
            # getting radius values
            self._get_rr()

        # updates subprofiles
        self._composite_subprofiles(other=other, operation=operation)
        self._profcalc()
        self._covcalc()

        self.hasprofile = True


def stacked_pcov(plist):
    """
    Calculates the Covariance between a list of profiles

    Only elements where both profile has at least 1 sources are considered, the rest is filled with placeholder
    values (-9999.0 by default).

    Parameters
    ----------
    plist: list of StackedProfileContainer objects
        profiles across whiche the covarainces are to be calcualted

    Returns
    -------
    np.array, np.array
        cov (E-mode), cov (B-mode)
    """
    # checking that input is of correct format
    # assert np.iterable(plist)
    # assert isinstance(plist[0], StackedProfileContainer)

    # data vectors for covariance
    dtvec = np.concatenate([pc.dst for pc in plist])
    dxvec = np.concatenate([pc.dsx for pc in plist])

    # lengths of bins
    dlen = len(dtvec)
    rlen = plist[0].nbin

    # container for the results
    supercov_t = np.ones(shape=(dlen, dlen)) * BADVAL
    supercov_x = np.ones(shape=(dlen, dlen)) * BADVAL

    # building up the covariance matrix
    for i1 in range(dlen):
        p1 = i1 // rlen  # the p-th profile
        pc1 = plist[p1]
        r1 = i1 % rlen  # the r-th radial bin within the p-th profile
        for i2 in range(dlen):
            p2 = i2 // rlen
            pc2 = plist[p2]
            r2 = i2 % rlen

            # calculating subpatches with data
            subind1 = pc1.sub_labels[np.where(pc1.subcounts[:, r1] > 1)[0]]
            subind2 = pc2.sub_labels[np.where(pc2.subcounts[:, r2] > 1)[0]]
            subind = list(set(subind1).intersection(set(subind2)))
            njk = len(subind)  # number of subpatches used
            if njk > 1:

                part1_t = (pc1.dst_sub[r1, subind] - pc1.dst[r1])
                part2_t = (pc2.dst_sub[r2, subind] - pc2.dst[r2])

                part1_x = (pc1.dsx_sub[r1, subind] - pc1.dsx[r1])
                part2_x = (pc2.dsx_sub[r2, subind] - pc2.dsx[r2])

                supercov_t[i1, i2] = np.sum(part1_t * part2_t) * (njk - 1.) / njk
                supercov_x[i1, i2] = np.sum(part1_x * part2_x) * (njk - 1.) / njk

    return supercov_t, supercov_x


def process_profile(fnames, ismeta=True, labels=None):
    """
    Extracts StackedProfileContainer from xshear output

    Parameters
    ----------
    fnames : list of str
        filenames to be read (without metatags)
    ismeta : bool
        whether to include METACALIBRATION files with :code:`sheared_tags`
    labels : list
        JK-labels for each entry in the lens files (in the order they are written to disk)

    Returns
    -------
    StackedProfileContainer
        Weak Lensing profile object

    """

    if type(fnames) is str or len(fnames) == 0:
        if type(fnames) is not str:
            fnames = fnames[0]
        cinfo, cdata, sheared_data = read_single_bin(fnames, ismeta)
    else:
        cinfo, cdata, sheared_data, labels = read_multiple_bin(fnames, ismeta)

    ncens = len(fnames)

    prof = StackedProfileContainer(cinfo, cdata, labels, ncens,
                                   metadata=sheared_data,
                                   metatags=sheared_tags)
    prof.prof_maker()

    return prof


def olivers_mock_function(a, b, c):
    """
    This is a one line description


    This is a multi line descrption of what this is::

        \sum_a = b \cdot c


    Parameters
    ----------
    a : int
        index
    b : float
        exponent of the expression
    c : np.ndarray
        dataset

    Returns
    -------
    dict
        The output

    """
    pass
