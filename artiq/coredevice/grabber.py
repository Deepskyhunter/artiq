from numpy import int32, int64

from artiq.language.core import *
from artiq.coredevice.rtio import rtio_output, rtio_input_data
from artiq.coredevice.core import Core


@nac3
class OutOfSyncException(Exception):
    """Raised when an incorrect number of ROI engine outputs has been
    retrieved from the RTIO input FIFO."""
    pass


@nac3
class Grabber:
    """Driver for the Grabber camera interface."""
    core: KernelInvariant[Core]
    channel_base: KernelInvariant[int32]
    sentinel: KernelInvariant[int32]

    def __init__(self, dmgr, channel_base, res_width=12, count_shift=0,
                 core_device="core"):
        self.core = dmgr.get(core_device)
        self.channel_base = channel_base

        count_width = min(31, 2*res_width + 16 - count_shift)
        # This value is inserted by the gateware to mark the start of a series of
        # ROI engine outputs for one video frame.
        self.sentinel = int32(int64(2**count_width))

    @kernel
    def setup_roi(self, n: int32, x0: int32, y0: int32, x1: int32, y1: int32):
        """
        Defines the coordinates of a ROI.

        The coordinates are set around the current position of the RTIO time
        cursor.

        The user must keep the ROI engine disabled for the duration of more 
        than one video frame after calling this function, as the output
        generated for that video frame is undefined.

        Advances the timeline by 4 coarse RTIO cycles.
        """
        c = int64(self.core.ref_multiplier)
        rtio_output((self.channel_base << 8) | (4*n+0), x0)
        delay_mu(c)
        rtio_output((self.channel_base << 8) | (4*n+1), y0)
        delay_mu(c)
        rtio_output((self.channel_base << 8) | (4*n+2), x1)
        delay_mu(c)
        rtio_output((self.channel_base << 8) | (4*n+3), y1)
        delay_mu(c)

    @kernel
    def gate_roi(self, mask: int32):
        """
        Defines which ROI engines produce input events.

        At the end of each video frame, the output from each ROI engine that
        has been enabled by the mask is enqueued into the RTIO input FIFO.

        This function sets the mask at the current position of the RTIO time
        cursor.

        Setting the mask using this function is atomic; in other words,
        if the system is in the middle of processing a frame and the mask
        is changed, the processing will complete using the value of the mask
        that it started with.

        :param mask: bitmask enabling or disabling each ROI engine.  
        """
        rtio_output((self.channel_base + 1) << 8, mask)

    @kernel
    def gate_roi_pulse(self, mask: int32, dt: float):
        """Sets a temporary mask for the specified duration (in seconds), before
        disabling all ROI engines."""
        self.gate_roi(mask)
        self.core.delay(dt)
        self.gate_roi(0)

    @kernel
    def input_mu(self, data: list[int32]):
        """
        Retrieves the accumulated values for one frame from the ROI engines.
        Blocks until values are available.

        The input list must be a list of integers of the same length as there
        are enabled ROI engines. This method replaces the elements of the
        input list with the outputs of the enabled ROI engines, sorted by
        number.

        If the number of elements in the list does not match the number of
        ROI engines that produced output, an exception will be raised during
        this call or the next.
        """
        channel = self.channel_base + 1

        sentinel = rtio_input_data(channel)
        if sentinel != self.sentinel:
            raise OutOfSyncException()

        for i in range(len(data)):
            roi_output = rtio_input_data(channel)
            if roi_output == self.sentinel:
                raise OutOfSyncException()
            data[i] = roi_output
