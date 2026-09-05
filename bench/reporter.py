# naub V2 input reporter - lives on the board as code.py, runs at boot.
# Streams one frame per ~20ms:  D<enc1>,<enc2>,<enc3>,<enc4>|<9 switch bits>
import rotaryio, digitalio, time
from microcontroller import pin as m

E = [(0, 1), (3, 4), (14, 15), (17, 18)]                  # ENC1..ENC4 A/B
S = [2, 5, 23, 19, 6, 7, 20, 21, 22]                      # 4 enc SW + BTN1..5

enc = [rotaryio.IncrementalEncoder(getattr(m, "GPIO%d" % a),
                                   getattr(m, "GPIO%d" % b)) for a, b in E]
sw = []
for n in S:
    d = digitalio.DigitalInOut(getattr(m, "GPIO%d" % n))
    d.direction = digitalio.Direction.INPUT
    d.pull = digitalio.Pull.UP
    sw.append(d)

print("RPT-READY")
while True:
    print("D%s|%s" % (",".join(str(e.position) for e in enc),
                      "".join("1" if not d.value else "0" for d in sw)))
    time.sleep(0.02)
