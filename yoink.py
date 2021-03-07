import requests
import random
import string
import os
import time
from requests_toolbelt import MultipartEncoder
import datetime
import enum
import getopt
import logging
import os
import subprocess
import sys
import shutil
import time
import streamlink
import ffmpeg
import requests
import config
import tempfile


class TwitchResponseStatus(enum.Enum):
    ONLINE = 0
    OFFLINE = 1
    NOT_FOUND = 2
    UNAUTHORIZED = 3
    ERROR = 4


class TwitchRecorder:
    def __init__(self):
        # global configuration
        self.ffmpeg_path = "ffmpeg"
        self.disable_ffmpeg = False
        self.refresh = 15
        self.root_path = ""

        # user configuration
        self.username = "spinningrecords"
        self.quality = "best"

        # twitch configuration
        self.client_id = "5b2dijiesh6wf21a9b7mcpy1fme9hx"
        self.client_secret = "sj8j7tenkejkh1dgvnk280qtlp2gxc"
        self.token_url = "https://id.twitch.tv/oauth2/token?client_id=" + self.client_id + "&client_secret=" \
                         + self.client_secret + "&grant_type=client_credentials"
        self.url = "https://api.twitch.tv/helix/streams"
        self.access_token = self.fetch_access_token()

    def fetch_access_token(self):
        token_response = requests.post(self.token_url, timeout=15)
        token_response.raise_for_status()
        token = token_response.json()
        return token["access_token"]

    def run(self):
        # path to recorded stream
        recorded_path = os.path.join(self.root_path, "recorded", self.username)
        # path to finished video, errors removed
        processed_path = os.path.join(
            self.root_path, "processed", self.username)

        # create directory for recordedPath and processedPath if not exist
        if os.path.isdir(recorded_path) is False:
            os.makedirs(recorded_path)
        if os.path.isdir(processed_path) is False:
            os.makedirs(processed_path)

        # make sure the interval to check user availability is not less than 15 seconds
        if self.refresh < 15:
            logging.warning(
                "check interval should not be lower than 15 seconds")
            self.refresh = 15
            logging.info("system set check interval to 15 seconds")

        # fix videos from previous recording session
        try:
            video_list = [f for f in os.listdir(
                recorded_path) if os.path.isfile(os.path.join(recorded_path, f))]
            if len(video_list) > 0:
                logging.info("processing previously recorded files")
            for f in video_list:
                recorded_filename = os.path.join(recorded_path, f)
                processed_filename = os.path.join(processed_path, f)
                self.process_recorded_file(
                    recorded_filename, processed_filename)
        except Exception as e:
            logging.error(e)

        logging.info("checking for %s every %s seconds, recording with %s quality",
                     self.username, self.refresh, self.quality)
        self.loop_check(recorded_path, processed_path)

    def process_recorded_file(self, recorded_filename, processed_filename):
        if self.disable_ffmpeg:
            logging.info("moving: %s", recorded_filename)
            shutil.move(recorded_filename, processed_filename)
        else:
            logging.info("fixing %s", recorded_filename)
            self.ffmpeg_copy_and_fix_errors(
                recorded_filename, processed_filename)

    def ffmpeg_copy_and_fix_errors(self, recorded_filename, processed_filename):
        try:
            subprocess.call(
                [self.ffmpeg_path, "-err_detect", "ignore_err", "-i", recorded_filename, "-c", "copy",
                 processed_filename])
            os.remove(recorded_filename)
        except Exception as e:
            logging.error(e)

    def check_user(self):
        info = None
        status = TwitchResponseStatus.ERROR
        try:
            headers = {"Client-ID": self.client_id,
                       "Authorization": "Bearer " + self.access_token}
            r = requests.get(self.url + "?user_login=" +
                             self.username, headers=headers, timeout=15)
            r.raise_for_status()
            info = r.json()
            if info is None or not info["data"]:
                status = TwitchResponseStatus.OFFLINE
            else:
                status = TwitchResponseStatus.ONLINE
        except requests.exceptions.RequestException as e:
            if e.response:
                if e.response.status_code == 401:
                    status = TwitchResponseStatus.UNAUTHORIZED
                if e.response.status_code == 404:
                    status = TwitchResponseStatus.NOT_FOUND
        return status, info

    def loop_check(self, recorded_path, processed_path):
        while True:
            status, info = self.check_user()
            if status == TwitchResponseStatus.NOT_FOUND:
                logging.error("username not found, invalid username or typo")
                time.sleep(self.refresh)
            elif status == TwitchResponseStatus.ERROR:
                logging.error("%s unexpected error. will try again in 5 minutes",
                              datetime.datetime.now().strftime("%Hh%Mm%Ss"))
                time.sleep(300)
            elif status == TwitchResponseStatus.OFFLINE:
                logging.info(
                    "%s currently offline, checking again in %s seconds", self.username, self.refresh)
                time.sleep(self.refresh)
            elif status == TwitchResponseStatus.UNAUTHORIZED:
                logging.info(
                    "unauthorized, will attempt to log back in immediately")
                self.access_token = self.fetch_access_token()
            elif status == TwitchResponseStatus.ONLINE:
                logging.info(
                    "%s online, stream recording in session", self.username)

                channels = info["data"]
                channel = next(iter(channels), None)
                filename = self.username + " - " + datetime.datetime.now() \
                    .strftime("%Y-%m-%d %Hh%Mm%Ss") + " - " + channel.get("title") + ".mp4"

                # clean filename from unnecessary characters
                filename = "".join(x for x in filename if x.isalnum() or x in [
                                   " ", "-", "_", "."])

                recorded_filename = os.path.join(recorded_path, filename)
                processed_filename = os.path.join(processed_path, filename)

                session = streamlink.Streamlink(options={
                    "hls-duration": 5,
                    "twitch-disable-ads": True
                })
                streams = session.streams("twitch.tv/" + self.username)
                stream = streams["audio_only"]

                stream_fd = stream.open()

                tmp_file_fd = open('.tmp', 'wb')

                input_data = stream_fd.read(1024)
                while(input_data):
                    tmp_file_fd.write(input_data)
                    input_data = stream_fd.read(1024)
                stream_fd.close()

                tmp_file_fd.close()
                tmp_file_fd = open('.tmp', 'rb')
                args = (ffmpeg
                    .input('pipe:')
                    # ... extra processing here
                    .output( 'pipe:', format="webm")
                    .get_args()
                )
                p = subprocess.Popen(['ffmpeg', '-vn'] + args, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
                buffer = p.communicate(input=tmp_file_fd.read(-1))[0]
                tmp_file_fd.close()

                logging.info("Done with extracting")
                fields = {
                    'tab_url': 'https://www.twitch.tv/',
                    'email': 'thanksforthe@api.com',
                    'google_id': '115067124769722938069',
                    'tab_title': 'Only for fun purposes',
                    'token': 'no_login',
                    'sample_bytes': len(buffer),
                    'timestamp': str(int(round(time.time() * 1000))),
                    'local_lan': 'en-US',
                    'browser_version': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/89.0.4389.72 Safari/537.36',
                    'device_id': '0.066957115950137871573742484313',
                    'version': '0.3.5',
                    'app_id': 'dpacanjfikmhoddligfbehkpomnbgblf'
                }

                r = requests.post("https://api.acrcloud.com/v1/aha-music/identify", verify=False,
                                  data=fields, files={'sample': ('juice.webm', buffer, 'audio/webm')})
                print(r.text)

                exit(0)

                logging.info("recording stream is done, processing video file")
                if os.path.exists(recorded_filename) is True:
                    self.process_recorded_file(
                        recorded_filename, processed_filename)
                else:
                    logging.info("skip fixing, file not found")

                logging.info("processing is done, going back to checking...")
                time.sleep(self.refresh)


if __name__ == "__main__":
    logging.basicConfig(filename="debug_info.log", level=logging.INFO)
    logging.getLogger().addHandler(logging.StreamHandler())
    TwitchRecorder().run()
