"""ST7565R display driver for the naub V2 panel (EA DOGM128W-6, 128x64).

Init sequence is the EA DOGM128-6 "single supply 3.0/3.3 V" application
circuit.  Measured on the V2 board (RP2350-Tiny, CircuitPython 10.3.0):

  * full-frame show_pages():  1.75 ms, 572 fps at 8 MHz SPI
  * full-frame show():        2.16 ms, 463 fps -- it slices the framebuffer
                              per page, and a slice on the right of an
                              expression allocates.  Use show_pages().
  * partial span():           ~2240 updates/s at 8 MHz (16px sprite, 2 pages)
  * 8 MHz SPI verified clean on hardware; the ST7565R tolerates it fine.

Faster is available and mostly unhelpful.  show_pages() measured 900 fps at
16 MHz and 1175 at 32; holding CS low for a whole frame instead of framing each
page adds another 10%.  But the panel is nowhere near the limit -- 1.75 ms out
of a 5.5 ms frame -- and an STN LCD cannot resolve 60 Hz anyway, which is
exactly why the FRC grayscale works.  The frame rate is set by what the UI
redraws, not by this file.

Framebuffer format (see gfx.py): bytearray(128*8), index = page*128 + x,
bit (1 << (y & 7)) with LSB = top row of the page.
"""
import busio, digitalio, time


class ST7565:
    W = 128
    PAGES = 8

    def __init__(self, sck, mosi, a0, cs, rst,
                 baudrate=8_000_000, orient_180=True, contrast=31,
                 col_offset=None):
        """Pins are board.GPx objects.  orient_180 flips the panel for the
        naub case orientation.  contrast is the 0x81 register (0..63);
        31 was tuned on the fitted panel for 4-plane FRC grays.

        col_offset shifts the RAM window, and DEPENDS ON THE ORIENTATION.  The
        ST7565R has 132 columns of RAM but this panel wires only 128 of them,
        as SEG4..SEG131.  With ADC reversed (0xA1, our non-flipped case) column
        address 0 maps to SEG131, so the window starts at 0; with ADC normal
        (0xA0, the flipped case) it maps to SEG0, so the window has to start at
        4.  Getting this wrong shifts the image by 4px and shows 4 columns of
        stale RAM at one edge -- invisible on screens with blank margins, plain
        as day on one that draws edge to edge.  Leave as None to derive it."""
        if col_offset is None:
            col_offset = 4 if orient_180 else 0
        self._spi = busio.SPI(sck, MOSI=mosi)
        while not self._spi.try_lock():
            pass
        self._spi.configure(baudrate=baudrate, polarity=0, phase=0)
        self._a0 = digitalio.DigitalInOut(a0)
        self._a0.switch_to_output(value=False)
        self._cs = digitalio.DigitalInOut(cs)
        self._cs.switch_to_output(value=True)
        self._rst = digitalio.DigitalInOut(rst)
        self._rst.switch_to_output(value=True)
        self.contrast = contrast
        self.col_offset = col_offset
        self.orient_180 = orient_180
        # prebuilt page-address commands and a reusable 3-byte scratch command
        # keep the hot paths allocation-free (GC pauses show up as flicker)
        self._page_cmds = [bytes((0xB0 | p,
                                  0x10 | ((col_offset >> 4) & 0x0F),
                                  col_offset & 0x0F)) for p in range(self.PAGES)]
        self._c3 = bytearray(3)
        self._zero_page = bytes(self.W)
        self.init()

    def cmd(self, *bs):
        self._a0.value = False
        self._cs.value = False
        self._spi.write(bytes(bs))
        self._cs.value = True

    def init(self):
        self._rst.value = False; time.sleep(0.005)
        self._rst.value = True;  time.sleep(0.005)
        self.cmd(0xE2); time.sleep(0.005)
        self.cmd(0x40)
        self.cmd(0xA0 if self.orient_180 else 0xA1)   # ADC select
        self.cmd(0xC8 if self.orient_180 else 0xC0)   # COM direction
        self.cmd(0xA6)            # normal (not inverted)
        self.cmd(0xA2)            # bias 1/9
        self.cmd(0x2F)            # booster+regulator+follower on
        self.cmd(0xF8, 0x00)      # booster 4x
        self.cmd(0x27)            # regulator ratio
        self.cmd(0x81, self.contrast)
        self.cmd(0xAC, 0x00)      # static indicator off
        self.cmd(0xAF)            # display on

    def set_contrast(self, v):
        self.contrast = max(0, min(0x3F, v))
        self.cmd(0x81, self.contrast)

    # ---- full-frame path -------------------------------------------------
    def show(self, fb):
        """Push a whole gfx framebuffer (accepts the raw bytearray)."""
        W = self.W
        for p in range(self.PAGES):
            self._a0.value = False; self._cs.value = False
            self._spi.write(self._page_cmds[p]); self._cs.value = True
            self._a0.value = True; self._cs.value = False
            self._spi.write(memoryview(fb)[p * W:(p + 1) * W])
            self._cs.value = True

    def make_views(self, fb):
        """Per-page memoryviews for show_pages() — build once, reuse forever."""
        W = self.W
        mv = memoryview(fb)
        return [mv[p * W:(p + 1) * W] for p in range(self.PAGES)]

    def show_pages(self, views):
        """Fastest full-frame path: views from make_views().  For FRC
        grayscale, cycle show_pages() over each plane's views in the main
        loop as fast as possible (see demos/demo_app_icons.py)."""
        for p in range(self.PAGES):
            self._a0.value = False; self._cs.value = False
            self._spi.write(self._page_cmds[p]); self._cs.value = True
            self._a0.value = True; self._cs.value = False
            self._spi.write(views[p]); self._cs.value = True

    # ---- partial-update path --------------------------------------------
    def span(self, page, buf, x, length=None):
        """Write `length` column bytes into one page starting at column x.
        buf may be bytes/bytearray/memoryview; allocation-free if length
        covers the whole buf.  ~0.45 ms per 2-page 16px sprite at 8 MHz."""
        c = x + self.col_offset
        c3 = self._c3
        c3[0] = 0xB0 | page
        c3[1] = 0x10 | (c >> 4)
        c3[2] = c & 0x0F
        self._a0.value = False; self._cs.value = False
        self._spi.write(c3); self._cs.value = True
        self._a0.value = True; self._cs.value = False
        if length is None or length == len(buf):
            self._spi.write(buf)
        else:
            self._spi.write(memoryview(buf)[:length])
        self._cs.value = True

    def clear(self):
        for p in range(self.PAGES):
            self.span(p, self._zero_page, 0)
