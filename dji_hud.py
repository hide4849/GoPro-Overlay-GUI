"""DJI-specific pictograms and Mode 1/2/3 stick visualization."""
import math

DJI_ITEMS = {
    'dji_battery': 'バッテリー残量（%）',
    'dji_temperature': 'バッテリー温度（℃）',
    'dji_voltage': 'バッテリー電圧（V）',
    'dji_current': 'バッテリー電流（A）',
    'dji_flight_time': '飛行経過時間（秒）',
    'dji_log_alt': '相対高度（m）',
    'dji_climb': '上昇・下降速度（km/h）',
    'dji_home_distance': 'ホーム地点からの距離（m）',
    'dji_distance': '累積移動距離（m）',
    'dji_satellites': 'GPS衛星数',
    'dji_uplink': '上り通信強度（5本アンテナ）',
    'dji_downlink': '下り通信強度（5本アンテナ）',
    'dji_mode': '飛行モード（N/S/C/M）',
    'dji_sticks': 'スティック位置（左右の枠）',
}


def mode_letter(value):
    return {'GPS_ATTI': 'N', 'GPS_NORMAL': 'N', 'GPS_SPORT': 'S',
            'SPORT': 'S', 'TRIPOD': 'C', 'CINEMATIC': 'C',
            'MANUAL': 'M'}.get(str(value), '--')


def signal_level(value):
    if value is None or not math.isfinite(value):
        return None
    return math.ceil(max(0, min(100, value)) / 20)


def stick_position(value):
    if value is None or not math.isfinite(value):
        return None
    return max(-1, min(1, (value - 1024) / 660))


def stick_axes(mode):
    return {1: (('rudder', 'elevator'), ('aileron', 'throttle')),
            2: (('rudder', 'throttle'), ('aileron', 'elevator')),
            3: (('aileron', 'elevator'), ('rudder', 'throttle'))}[mode]


class DJIWidget:
    def __init__(self, element, entry, font):
        self.entry = entry
        self.font = font
        self.x = int(element.get('x', 0))
        self.y = int(element.get('y', 0))
        self.scale = float(element.get('scale', 1))
        self.kind = element.get('type')
        self.key = element.get('metric')
        self.label = element.get('label')
        self.mode = int(element.get('stick_mode', 2))

    def draw(self, image, draw):
        e = self.entry()
        s, x, y = self.scale, self.x, self.y
        def box(a, b, c, d):
            return [(round(x+a*s), round(y+b*s)), (round(x+c*s), round(y+d*s))]
        if self.kind == 'dji_signal':
            q = getattr(e, self.key)
            count = signal_level(q.magnitude if q is not None else None)
            draw.text((x, y), self.label or ('UPLINK' if self.key == 'dji_uplink' else 'DOWNLINK'), font=self.font, fill=(180, 203, 218))
            bottom = max(56, self.font.size / s + 50)
            for i in range(5):
                height = 10 + i * 7
                draw.rounded_rectangle(box(i*18, bottom-height, i*18+12, bottom), radius=max(1,round(2*s)),
                                       fill=(104,226,231) if count is not None and i < count else (57,73,86))
            if count is None:
                draw.text((round(x+98*s),round(y+28*s)), '--',font=self.font,fill=(180,203,218))
        elif self.kind == 'dji_mode':
            draw.rounded_rectangle(box(0,0,68,52),radius=max(1,round(9*s)),fill=(12,22,34,210),outline=(104,226,231))
            draw.text((round(x+14*s),round(y+6*s)),mode_letter(e.dji_mode),font=self.font,fill=(104,226,231))
        elif self.kind == 'dji_sticks':
            for i, (horizontal, vertical) in enumerate(stick_axes(self.mode)):
                bx = 148*i
                draw.rounded_rectangle(box(bx,0,bx+124,124),radius=max(1,round(8*s)),fill=(12,22,34,180),outline=(180,203,218),width=max(1,round(2*s)))
                draw.line(box(bx+62,10,bx+62,114),fill=(57,73,86),width=max(1,round(s)))
                draw.line(box(bx+10,62,bx+114,62),fill=(57,73,86),width=max(1,round(s)))
                hx = getattr(e, 'dji_rc_'+horizontal)
                vy = getattr(e, 'dji_rc_'+vertical)
                dx = stick_position(hx.magnitude if hx is not None else None)
                dy = stick_position(vy.magnitude if vy is not None else None)
                if dx is not None and dy is not None:
                    cx,cy=bx+62+dx*50,62-dy*50
                    draw.ellipse(box(cx-6,cy-6,cx+6,cy+6),fill=(104,226,231),outline=(255,255,255))
                else:
                    draw.text((round(x+(bx+48)*s),round(y+50*s)), '--',font=self.font,fill=(180,203,218))
                draw.text((round(x+(bx+48)*s),round(y+128*s)), 'L' if i == 0 else 'R',font=self.font,fill=(180,203,218))


def create_hud_widget(factory, element, entry, **kwargs):
    return DJIWidget(element, entry, factory._font(element, 'size', d=16))
