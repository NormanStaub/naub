# naub V2 firmware - boot config
#
# Enable a SECOND USB CDC interface (the "data" port) alongside the console.
# The host bridge talks the naub line protocol over that data port; the console
# port stays free for print()/REPL debugging.
#
# This matters more on V2 than it did on V1: host->device writes on the CONSOLE
# port hang on this board (a V2 bring-up finding), so the data port is the only
# way in.
#
# USB descriptors are fixed when the device enumerates, so this MUST live in
# boot.py (which runs once at power-on) and the board must be FULLY POWER
# CYCLED after copying it -- a soft reload will not do. Two serial ports should
# then appear instead of one.
import usb_cdc

usb_cdc.enable(console=True, data=True)
