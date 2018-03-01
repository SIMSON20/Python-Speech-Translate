# -*- coding: utf-8 -*-

"""
DOA
"""

import collections
import os
import sys
import threading
if sys.version_info[0] < 3:
    import Queue as queue
else:
    import queue

import numpy as np
from webrtc_audio_processing import AP
from voice_engine.element import Element

from pixel_ring import pixel_ring

import mraa
import os
import time

en = mraa.Gpio(12)
if os.geteuid() != 0 :
    time.sleep(1)
 
en.dir(mraa.DIR_OUT)
en.write(0)

pixel_ring.set_brightness(20)

eps = np.finfo(float).eps


class DOA(Element):
    def __init__(self, channels=8, ns=True, agc=0):
        super(DOA, self).__init__()

        self.channels = channels
        self.mask = [0, 1, 2, 3, 4, 5]
        self.pair = [[0, 3], [1, 4], [2, 5]]

        self.frame_size = 160
        self.frame_bytes = self.frame_size * self.channels * 2

        self.ap = AP(enable_ns=ns, agc_type=agc, enable_vad=True)
        self.ap.set_stream_format(16000, 1)

        self.queue = queue.Queue()
        self.done = False

        self.collections = collections.deque(maxlen=16)

        # prepare hanning window for stft
        self.window = np.hanning(self.frame_size)
        # self.window = None

        # length of stft
        self.nfft = 1 << (self.frame_size * 2 - 1).bit_length()
        print('fft size: {}'.format(self.nfft))
        # self.nfft = 512

        self.margin_f = 0.064 * 16000 / 340.0

        self.interp = 2
        self.margin = int(self.margin_f * self.interp)

        self.cc_baseline = [0] * len(self.pair)

    def put(self, data):
        self.queue.put(data)

    def start(self):
        self.done = False
        thread = threading.Thread(target=self.run)
        thread.daemon = True
        thread.start()

    def stop(self):
        pixel_ring.off()
        self.done = True

    def run(self):
        has_voice = 0
        buffer = ''
        count = 0
        pixel_ring_countdown = 0

        while not self.done:
            data = self.queue.get()
            buffer += data

            while len(buffer) >= self.frame_bytes:
                data = buffer[:self.frame_bytes]
                buffer = buffer[self.frame_bytes:]

                data = np.fromstring(data, dtype='int16')
                mono = data[0::self.channels].tostring()

                mono = self.ap.process_stream(mono)
                has_voice = self.ap.has_voice()

                # sys.stdout.write('1' if has_voice else '0')
                # sys.stdout.flush()

                offset, direction = self._process(data)

                self.collections.append([direction, offset, has_voice])

                count += 1
                if count >= self.collections.maxlen:
                    direction = self.get_direction()
                    if direction:
                        print('@ {}'.format(direction))

                        pixel_ring.wakeup(direction)
                        pixel_ring_countdown = 10
                    else:
                        if pixel_ring_countdown > 0:
                            pixel_ring_countdown -= 1
                            if pixel_ring_countdown == 0:
                                pixel_ring.off()

                    count = 0

                super(DOA, self).put(mono)

    def set_callback(self, callback):
        if callable(callback):
            self.on_detected = callback
        else:
            ValueError('The callback parameter is not callable')

    def get_direction(self):
        counting = [0] * 12
        voice = 0
        for d in self.collections:
            if d[2]:
                voice += 1

            counting[d[0]] += 1

        direction_index = np.argmax(counting)
        self.direction = direction_index * 30

        # print counting[direction_index], voice

        if voice >= self.collections.maxlen / 2 and counting[direction_index] >= self.collections.maxlen / 3:
            return self.direction

    def _process(self, data):
        X = [0] * self.channels
        for channel in self.mask:
            x = data[channel::self.channels]
            # add window
            if self.window is not None:
                x = x * self.window

            X[channel] = np.fft.rfft(x, self.nfft)

        offset = [0] * len(self.pair)

        for i, v in enumerate(self.pair):
            CC = X[v[1]] * np.conj(X[v[0]])
            # generalized
            CC /= np.abs(CC) + eps
            cc = np.fft.irfft(CC, n=self.nfft * self.interp)

            cc = np.concatenate((cc[-self.margin:], cc[:self.margin + 1]))

            cc = np.abs(cc)

            cc = cc - self.cc_baseline[i]

            # find max cross correlation index
            offset_max = np.argmax(cc) - self.margin
            offset[i] = (offset_max) / float(self.interp)

            # update baseline
            self.cc_baseline[i] = self.cc_baseline[i] + 0.01 * cc

        # if offset[0] == 0 and offset[1] == 0 and offset[2] == 0:
        #     print cc_array

        min_index = np.argmin(np.abs(offset[:3]))
        theta = np.arcsin(offset[min_index] / self.margin_f) * 180 / np.pi
        if (min_index != 0 and offset[min_index - 1] < 0) or (min_index == 0 and offset[2] >= 0):
            best_guess = (360 - theta) % 360
        else:
            best_guess = (180 + theta)

        best_guess = (best_guess + 30 + min_index * 60) % 360

        direction = int((best_guess + 15) // 30 % 12)

        return offset, direction


def main():
    import time
    from voice_engine.source import Source

    src = Source(channels=8, rate=16000, frames_size=160)
    doa = DOA(channels=8)

    src.link(doa)

    doa.start()
    src.start()

    while True:
        try:
            time.sleep(1)
        except KeyboardInterrupt:
            break

    doa.stop()
    src.stop()

    pixel_ring.off()


if __name__ == '__main__':
    main()
