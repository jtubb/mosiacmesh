"""One-shot diagnostic: per-marker reported aspect vs flood-fill band aspect."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cv2 as cv, numpy as np, jsonpickle
from server import (detect_aruco_markers, _band_from_marker_floodfill,
                    _aspect_in_marker_frame)

img = cv.imread(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              'cache', '20260601_162307.jpg'))
corners, ids, _ = detect_aruco_markers(img)
obj = jsonpickle.decode(open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                            'settings.dat')).read())
ar_info = {int(c.get('arucoID')): (c.get('friendlyName', '') or k[:8],
                                     c.get('canvasWidth', 0), c.get('canvasHeight', 0))
           for k, c in obj.clients.items() if c.get('arucoID') is not None}

print(f'{"name":<16} {"reported":>9} {"rep_asp":>7} {"band_asp":>8} {"verdict":<6}')
print('-' * 54)
of_interest = {'sign1screen1', 'sign1screen6', 'sign1screen11',
               'sign1screen13', 'sign1screen16', 'sign1screen18'}
rows = []
for mc, mid in zip(corners, ids.flatten()):
    info = ar_info.get(int(mid))
    if not info:
        continue
    fn, cw, ch = info
    band = _band_from_marker_floodfill(img, mc.reshape(4, 2))
    if band is None:
        rows.append((fn, cw, ch, None))
        continue
    ba = _aspect_in_marker_frame(band, mc.reshape(4, 2))
    rows.append((fn, cw, ch, ba))

rows.sort(key=lambda r: r[0])
for fn, cw, ch, ba in rows:
    rep = float(cw) / max(1.0, float(ch))
    flag = '  <==' if fn in of_interest else ''
    if ba is None:
        print(f'{fn:<16} {cw}x{ch:<5} {rep:>7.2f} {"NO BAND":>8} -{flag}')
        continue
    swap = abs(np.log(ba) - np.log(1.0 / rep)) < abs(np.log(ba) - np.log(rep))
    verdict = "SWAP" if swap else "keep"
    print(f'{fn:<16} {cw}x{ch:<5} {rep:>7.2f} {ba:>8.2f} {verdict:<6}{flag}')
