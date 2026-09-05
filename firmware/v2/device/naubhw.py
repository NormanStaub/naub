"""naub V2 board wiring — one import gives you display, backlight, encoders,
buttons with all the bring-up lessons baked in (PCB_DESIGN.md section 1 pin map).

    import naubhw
    disp = naubhw.display()          # ST7565 at 8 MHz, contrast 31, 180 flip
    bl   = naubhw.Backlight()        # bl.set(80)
    encs = naubhw.encoders()         # [ENC1..ENC4], one count per detent
    sws  = naubhw.encoder_switches() # [SW1..SW4] DigitalInOut, pulled up
    btns = naubhw.buttons()          # [BTN1..BTN5]

Lessons encoded here:
  * rotaryio divisor=2, NOT the default 4 — the fitted PEC11L-4215F-S0015 is
    30-detent/15-PPR; divisor=4 skips every other detent.
  * GP16 is spent on the module's onboard WS2812; GP17-GP23 live on the
    module's back solder pads and are NOT in `board.*` — they must come from
    microcontroller.pin (ENC3_SW, ENC4, BTN3-5).
  * All switch/encoder inputs use internal pull-ups (no discrete pull-ups on
    the board, by design — PCB_DESIGN.md 2.1).
"""
import board
import digitalio, pwmio, rotaryio
from microcontroller import pin as mpin

from st7565 import ST7565

# physical placement (top row left->right, then lower-right): ENC1=top-left,
# ENC2=top-middle, ENC3=top-right, ENC4=lower-right
ENC_AB = ((board.GP0, board.GP1),          # ENC1
          (board.GP3, board.GP4),          # ENC2
          (board.GP14, board.GP15),        # ENC3
          (mpin.GPIO17, mpin.GPIO18))      # ENC4 (back pads)
ENC_SW = (board.GP2, board.GP5, mpin.GPIO23, mpin.GPIO19)
BTN = (board.GP6, board.GP7, mpin.GPIO20, mpin.GPIO21, mpin.GPIO22)

LCD_A0, LCD_CS, LCD_SCK, LCD_MOSI, LCD_RST = (
    board.GP8, board.GP9, board.GP10, board.GP11, board.GP12)
BL_PWM = board.GP13

ENCODER_DIVISOR = 2        # PEC11L 30-detent/15-PPR: one count per detent


def display(baudrate=8_000_000, contrast=31, orient_180=True):
    return ST7565(LCD_SCK, LCD_MOSI, LCD_A0, LCD_CS, LCD_RST,
                  baudrate=baudrate, orient_180=orient_180, contrast=contrast)


class Backlight:
    def __init__(self, pct=80, frequency=20000):
        self._pwm = pwmio.PWMOut(BL_PWM, frequency=frequency, duty_cycle=0)
        self.set(pct)

    def set(self, pct):
        self.pct = max(0, min(100, pct))
        self._pwm.duty_cycle = self.pct * 65535 // 100


def encoders(which=(0, 1, 2, 3)):
    return [rotaryio.IncrementalEncoder(ENC_AB[i][0], ENC_AB[i][1],
                                        divisor=ENCODER_DIVISOR)
            for i in which]


def _input(pin):
    d = digitalio.DigitalInOut(pin)
    d.switch_to_input(pull=digitalio.Pull.UP)
    return d                                   # pressed == not d.value


def encoder_switches(which=(0, 1, 2, 3)):
    return [_input(ENC_SW[i]) for i in which]


def buttons(which=(0, 1, 2, 3, 4)):
    return [_input(BTN[i]) for i in which]
