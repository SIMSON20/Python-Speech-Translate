"""
Python sample demonstrating use of Microsoft Translator Speech Translation API with Seeed ReSpeaker V2.
"""

import os
import StringIO
import struct
import threading
import time
import uuid
import wave
import sys
import Queue
import json
import subprocess

import websocket

from auth import AzureAuthClient

from voice_engine.source import Source
from voice_engine.element import Element
from doa import DOA


class AudioBuffer(Element):
    def __init__(self):
        super(AudioBuffer, self).__init__()

        self.queue = Queue.Queue(maxsize=100)

    def put(self, data):
        self.queue.put(data)


class Player(object):
    def __init__(self):
        pass

    def play(self, data):
        with open('/tmp/speech.wav', 'w') as f:
            f.write(data)

        subprocess.call('aplay -Dplughw:0,1 /tmp/speech.wav', shell=True)
        #self.aplay = subprocess.Popen('aplay -', stdin=subprocess.PIPE, shell=True)
        #self.aplay.stdin.write(data)


def get_wave_header(frame_rate):
    """
    Generate WAV header that precedes actual audio data sent to the speech translation service.

    :param frame_rate: Sampling frequency (8000 for 8kHz or 16000 for 16kHz).
    :return: binary string
    """

    if frame_rate not in [8000, 16000]:
        raise ValueError("Sampling frequency, frame_rate, should be 8000 or 16000.")

    nchannels = 1
    bytes_per_sample = 2

    output = StringIO.StringIO()
    output.write('RIFF')
    output.write(struct.pack('<L', 0))
    output.write('WAVE')
    output.write('fmt ')
    output.write(struct.pack('<L', 18))
    output.write(struct.pack('<H', 0x0001))
    output.write(struct.pack('<H', nchannels))
    output.write(struct.pack('<L', frame_rate))
    output.write(struct.pack('<L', frame_rate * nchannels * bytes_per_sample))
    output.write(struct.pack('<H', nchannels * bytes_per_sample))
    output.write(struct.pack('<H', bytes_per_sample * 8))
    output.write(struct.pack('<H', 0))
    output.write('data')
    output.write(struct.pack('<L', 0))

    data = output.getvalue()

    output.close()

    return data


if __name__ == "__main__":

    quit_event = threading.Event()

    client_secret = 'INSERT YOUR CLIENT SECRET'
    auth_client = AzureAuthClient(client_secret)

    # src = Source(rate=16000, frames_size=160)

    src = Source(channels=8, rate=16000, frames_size=160)
    doa = DOA(channels=8)

    audio_buffer = AudioBuffer()

    src.pipeline(doa, audio_buffer)

    player = Player()

    # Translate from this language. The language must match the source audio.
    # Supported languages are given by the 'speech' scope of the supported languages API.
    translate_from = 'en-US'
    # Translate to this language.
    # Supported languages are given by the 'text' scope of the supported languages API.
    translate_to = 'zh-HANS'
    # Features requested by the client.
    features = "Partial,TextToSpeech"

    # Transcription results will be saved into a new folder in the current directory
    output_folder = os.path.join(os.getcwd(), uuid.uuid4().hex)

    # These variables keep track of the number of text-to-speech segments received.
    # Each segment will be saved in its own audio file in the output folder.
    tts_state = {'count': 0}

    # Setup functions for the Websocket connection

    def on_open(ws):
        """
        Callback executed once the Websocket connection is opened.
        This function handles streaming of audio to the server.

        :param ws: Websocket client.
        """

        print 'Connected. Server generated request ID = ', ws.sock.headers['x-requestid']

        def run(*args):
            """Background task which streams audio."""

            # Send WAVE header to provide audio format information
            # data = get_wave_header(audio_source.getframerate())

            data = get_wave_header(16000)

            ws.send(data, websocket.ABNF.OPCODE_BINARY)

            while not quit_event.is_set():
                data = audio_buffer.queue.get()
                ws.send(data, websocket.ABNF.OPCODE_BINARY)

            ws.close()
            print 'Background thread terminating...'

        threading.Thread(target=run).start()

    def on_close(ws):
        """
        Callback executed once the Websocket connection is closed.

        :param ws: Websocket client.
        """
        print 'Connection closed...'

    def on_error(ws, error):
        """
        Callback executed when an issue occurs during the connection.

        :param ws: Websocket client.
        """
        print error

    def on_data(ws, message, message_type, fin):
        """
        Callback executed when Websocket messages are received from the server.

        :param ws: Websocket client.
        :param message: Message data as utf-8 string.
        :param message_type: Message type: ABNF.OPCODE_TEXT or ABNF.OPCODE_BINARY.
        :param fin: Websocket FIN bit. If 0, the data continues.
        """

        if message_type == websocket.ABNF.OPCODE_TEXT:
            msg = json.loads(message, encoding='utf8')
            print msg['type'], '[en]: ', msg['recognition'], ', [zh]:', msg['translation']
        else:
            player.play(message)

    def connect():
        client_trace_id = str(uuid.uuid4())
        request_url = "wss://dev.microsofttranslator.com/speech/translate?from={0}&to={1}&features={2}&api-version=1.0".format(translate_from, translate_to, features)

        print "Ready to connect..."
        print "Request URL      = {0})".format(request_url)
        print "ClientTraceId    = {0}".format(client_trace_id)
        print 'Results location = %s\n' % (output_folder)

        ws_client = websocket.WebSocketApp(
            request_url,
            header=[
                'Authorization: Bearer ' + auth_client.get_access_token(),
                'X-ClientTraceId: ' + client_trace_id
            ],
            on_open=on_open,
            on_data=on_data,
            on_error=on_error,
            on_close=on_close
        )
        ws_client.run_forever()

    threading.Thread(target=connect).start()

    src.pipeline_start()
    while True:
        try:
            time.sleep(1)
        except KeyboardInterrupt:
            break

    quit_event.set()
    src.pipeline_stop()

