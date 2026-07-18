# naub firmware - boot config
#
# Enable a SECOND USB CDC interface (the "data" port) alongside the normal
# console/REPL port. The host helper talks the naub protocol over this data
# port; the console port stays free for print()/REPL debugging.
#
# This MUST live in boot.py (runs once at power-on, before USB is brought up).
# After copying, fully power-cycle the board so the new USB descriptor enumerates
# -- two COM ports / tty devices should then appear.
import usb_cdc
usb_cdc.enable(console=True, data=True)
