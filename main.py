import logging
import re
from time import sleep, strptime, mktime
from datetime import datetime
from random import randrange
from typing import List, Optional
import json
from io import BytesIO, StringIO
from lxml import etree
from os import getenv
from sys import stdout

from requests_html import HTMLSession
from requests import Response
from requests.exceptions import ConnectionError
import sentry_sdk
from sentry_sdk import capture_exception, capture_message
import toml
import boto3
from botocore.exceptions import ClientError
from feedgen.feed import FeedGenerator
from slugify import slugify
from retry import retry


class TimeOutException(Exception):
    pass


class Spider:
    def __init__(self):
        self.config = toml.load("config.toml")
        self.aws_session = boto3.session.Session()
        self.signal_run()

        self.environment = getenv("SENTRY_ENVIRONMENT", "development")
        with open("version.txt", "r") as f:
            self.version = f.readline().strip()

        loglevel = logging.DEBUG
        if self.environment == "production":
            loglevel = logging.ERROR

        logging.basicConfig(
            stream=stdout,
            level=loglevel,
            format="%(asctime)s - %(levelname)s - %(message)s",
        )

        sentry_sdk.init(
            "https://f9af5d4b88bb4df1a182849a4387c61e@sentry.io/1078203",
            environment=self.environment,
            release=self.version,
        )

        self.feed = FeedGenerator()
        self.session = HTMLSession()
        self.s3 = self.aws_session.client(service_name="s3")

    def _anti_hammer_sleep(self):
        logging.debug("zzzZZzzzZZZZZzzzzz")
        sleep(randrange(1, self.config["anti_hammer_sleep"]))

    def _secrets(self):
        logging.debug("fetching secrets from AWS")
        try:
            client = self.aws_session.client(
                service_name="secretsmanager",
                region_name=self.config["secretsmanager"]["region"],
            )
            get_secret_value_response = client.get_secret_value(
                SecretId=self.config["secretsmanager"]["secret_name"]
            )
        except ClientError as e:
            capture_exception(e)

            if e.response["Error"]["Code"] == "DecryptionFailureException":
                # Secrets Manager can't decrypt the protected secret text using the provided KMS key.
                # Deal with the exception here, and/or rethrow at your discretion.
                raise e
            elif e.response["Error"]["Code"] == "InternalServiceErrorException":
                # An error occurred on the server side.
                # Deal with the exception here, and/or rethrow at your discretion.
                raise e
            elif e.response["Error"]["Code"] == "InvalidParameterException":
                # You provided an invalid value for a parameter.
                # Deal with the exception here, and/or rethrow at your discretion.
                raise e
            elif e.response["Error"]["Code"] == "InvalidRequestException":
                # You provided a parameter value that is not valid for the current state of the resource.
                # Deal with the exception here, and/or rethrow at your discretion.
                raise e
            elif e.response["Error"]["Code"] == "ResourceNotFoundException":
                # We can't find the resource that you asked for.
                # Deal with the exception here, and/or rethrow at your discretion.
                raise e
        else:
            # Decrypts secret using the associated KMS CMK.
            # Depending on whether the secret is a string or binary, one of these fields will be populated.
            if "SecretString" in get_secret_value_response:
                return json.loads(get_secret_value_response["SecretString"])

    def crawl(self):
        self._login()

        self.feed.id(f"{self.version}.vadviktor.xyz")
        self.feed.updated(datetime.utcnow().isoformat("T") + "Z")
        self.feed.author(
            {
                "name": "Viktor (Ikon) VAD",
                "email": "vad.viktor@gmail.com",
                "uri": "https://www.github.com/vadviktor",
            }
        )
        self.feed.title("Animetorrents.me feed")
        self.feed.link(
            href=self.config["s3"]["object_url"].format(
                bucket=self.config["s3"]["bucket"],
                region=self.config["s3"]["region"],
                filekey=self.config["s3"][f"feed_filename_{self.environment}"],
            ),
            rel="self",
        )

        for profile_url in self._torrent_profile_links(self._max_pages()):
            profile_data = self._parse_profile(profile_url)
            if profile_data is None:
                continue

            fe = self.feed.add_entry(order="append")
            fe.id(profile_url)
            fe.title(profile_data["title"])
            fe.link(href=profile_url, rel="self")

            cover_image_url = None
            if profile_data["cover_image_src"] is not None:
                cover_image_url = self._cover_image_upload_and_get_url(
                    profile_data["cover_image_src"]
                )

            thumbnail_small_image_urls = self._thumbnail_small_image_upload_and_get_urls(
                profile_data["thumbnail_small_image_srcs"]
            )
            thumbnail_large_image_urls = self._thumbnail_large_image_upload_and_get_urls(
                profile_data["thumbnail_large_image_srcs"]
            )

            torrent_public_url = self._torrent_upload_and_get_url(
                profile_data["torrent_download_url"],
                profile_data["torid"],
                slugify(profile_data["title"]),
                profile_data["publish_date"],
            )

            content_lines = []
            if cover_image_url is not None:
                content_lines.append(f'<p><img src="{cover_image_url}" /></p>')

            content_lines.append(f'<p>[{profile_data["category"]}]</p>')
            content_lines.append(f'<p>Tags: {profile_data["tags"]}</p>')
            content_lines.append(f'<p>Published: {profile_data["publish_date"]}</p>')
            content_lines.append(
                f'<p><a href="{profile_url}" target="blank">{profile_url}</a></p>'
            )
            content_lines.append(
                f'<p style="white-space: pre-wrap;">{profile_data["description"]}</p>'
            )

            content_lines.append(f"<p>")
            for k, v in enumerate(thumbnail_small_image_urls):
                content_lines.append(
                    f"""
                    <a href="{thumbnail_large_image_urls[k]}" target="blank">
                        <img src="{v}" width="200" height="100" />
                    </a>"""
                )
            content_lines.append(f"</p>")

            content_lines.append(
                f'<p><a href="{torrent_public_url}" target="blank">Download</a></p>'
            )
            content_lines.append(f'<p>{profile_data["file_list"]}</p>')

            if profile_data["media_info"] is not None:
                content_lines.append(f'<p>{profile_data["media_info"]}</p>')

            fe.content(self._valid_xhtml_content(content_lines), type="xhtml")

        self._upload_feed()

    @staticmethod
    def _valid_xhtml_content(content_lines: List) -> str:
        broken_html = "".join(content_lines)
        # parse as HTML
        parser = etree.HTMLParser()
        tree = etree.parse(StringIO(broken_html), parser)
        # output as valid XML
        result = etree.tostring(tree.getroot(), pretty_print=True, method="xml")

        return result.decode("utf-8")

    def _upload_feed(self):
        logging.debug("construct and upload feed")

        atomfeed = self.feed.atom_str()
        bucket = self.config["s3"]["bucket"]
        key = self.config["s3"][f"feed_filename_{self.environment}"].format(
            version=self.version
        )
        self.s3.upload_fileobj(BytesIO(atomfeed), bucket, key)
        resp = self.s3.put_object_acl(ACL="public-read", Bucket=bucket, Key=key)
        if resp is None:
            capture_message(f"Failed to set object ACL for {bucket}/{key}")

    def _parse_profile(self, profile_url):
        logging.debug(f"processing profile {profile_url}")
        resp = self._get(profile_url)

        if (
            "Error 404: Torrent not found" in resp.text
            or "Torrent not found" in resp.text
        ):
            msg = f"No torrent found for {profile_url}"
            logging.info(msg)
            capture_message(msg)
            return None

        profile_data = {}
        profile_data["category"] = resp.html.find("h1.headline img", first=True).attrs[
            "alt"
        ]
        if any(
            category in profile_data["category"]
            for category in self.config["exclude_categories"]
        ):
            return None

        profile_data["torid"] = re.match(r".*=(\d+)$", profile_url)[1]

        try:
            profile_data["torrent_download_url"] = next(
                l for l in resp.html.links if "download.php?torid=" in l
            )
        except StopIteration:
            msg = f"did not find download link for {profile_url}"
            capture_message(msg)
            raise RuntimeError(msg)

        profile_data["hashid"] = re.match(
            r".*torid=([a-z0-9]+)$", profile_data["torrent_download_url"]
        ).group(1)

        profile_data["title"] = resp.html.find("h1.headline", first=True).text
        profile_data["description"] = resp.html.find("#torDescription", first=True).text
        profile_data["tags"] = resp.html.find("#tagLinks", first=True).text
        profile_data["publish_date"] = self._parse_publish_date(
            resp.html.find("div.ribbon span.blogDate", first=True).text
        )
        profile_data["media_info"] = self._download_media_info(profile_data["torid"])
        profile_data["file_list"] = self._download_file_list(profile_data["hashid"])

        try:
            profile_data["cover_image_src"] = next(
                link.attrs["src"]
                for link in resp.html.find("div.contentArea img")
                if "imghost/covers/" in link.attrs["src"]
            )
        except StopIteration:
            logging.debug(f"did not find cover image for {profile_url}")
            profile_data["cover_image_src"] = None

        profile_data["thumbnail_small_image_srcs"] = [
            i.attrs["src"] for i in resp.html.find("#torScreens img")
        ]
        profile_data["thumbnail_large_image_srcs"] = [
            i.attrs["href"] for i in resp.html.find("#torScreens a")
        ]

        return profile_data

    @retry((TimeOutException, ConnectionError), tries=5, delay=3, backoff=2)
    def _get(self, url, **kwargs) -> Response:
        self._anti_hammer_sleep()
        resp = self.session.get(url, **kwargs)

        # this site uses CloudFlare and could get gateway error, but can be retried
        if resp.status_code == 502:
            raise TimeOutException

        if resp.status_code == 504:
            raise TimeOutException

        return resp

    @staticmethod
    def _parse_publish_date(text) -> datetime:
        return datetime.fromtimestamp(mktime(strptime(text, "%d %b, %Y [%I:%M %p]")))

    def _torrent_profile_links(self, max_pages) -> List:
        links = []
        for page in range(1, self.config["torrent_pages_to_scan"] + 1):
            resp = self._torrent_list_response(page, max_pages)

            [
                links.append(l)
                for l in resp.html.links
                if "torrent-details.php?torid=" in l
            ]

        return links

    @retry(TimeOutException, tries=5, delay=3, backoff=2)
    def _torrent_list_response(self, current_page: int, max_pages: int) -> Response:
        logging.debug(f"getting torrent list page no. {current_page}")
        headers = {"X-Requested-With": "XMLHttpRequest"}
        url = self.config["site"]["torrent_list_url"].format(
            max=max_pages, current=current_page
        )
        resp = self._get(url=url, headers=headers)
        if resp.status_code == 504:
            raise TimeOutException

        logging.debug(f"response status code {resp.status_code}")
        logging.debug(f"response length {len(resp.text)}")

        if "Access Denied!" in resp.text:
            raise RuntimeError("AJAX request was denied")

        return resp

    def _login(self):
        login_url = self.config["site"]["login_url"]
        username = self._secrets()["username"]
        password = self._secrets()["password"]

        self._get(login_url)
        resp = self.session.post(
            login_url,
            data={"form": "login", "username": username, "password": password},
        )

        if "Error: Invalid username or password." in resp.text:
            raise RuntimeError("login failed because of invalid credentials")
        else:
            logging.debug("logged in")

    def _max_pages(self):
        logging.debug("finding out torrents max page number")

        try:
            resp = self._get(self.config["site"]["torrents_url"])
            if resp.status_code != 200:
                raise RuntimeError("the torrents page is not responding correctly")

            pattern = r"ajax/torrents_data\.php\?total=(?P<max>\d+)&page=1"
            match = re.search(pattern, resp.text)
            if match is None:
                raise RuntimeError("could not find max page number")

            max_page = match.group("max")
            logging.debug(f"max pages figured out: {max_page}")

            return int(max_page)
        except ConnectionError as e:
            capture_exception(e)
            raise RuntimeError("failed to get the torrents page")

    def _download_media_info(self, torid) -> Optional[str]:
        logging.debug(f"getting torrent media info for {torid}")

        headers = {"X-Requested-With": "XMLHttpRequest"}
        url = self.config["site"]["torrent_techspec_url"].format(torid)
        resp = self._get(url=url, headers=headers)

        logging.debug(f"response status code {resp.status_code}")
        logging.debug(f"response length {len(resp.text)}")

        if len(resp.text) == 0:
            return None

        if "Access Denied!" in resp.text:
            raise RuntimeError("AJAX request was denied")

        return resp.html.html

    def _download_file_list(self, hashid) -> str:
        logging.debug(f"getting torrent file list for {hashid}")

        headers = {"X-Requested-With": "XMLHttpRequest"}
        url = self.config["site"]["torrent_filelist_url"].format(hashid)
        resp = self._get(url=url, headers=headers)

        logging.debug(f"response status code {resp.status_code}")
        logging.debug(f"response length {len(resp.text)}")

        if "Access Denied!" in resp.text:
            raise RuntimeError("AJAX request was denied")

        return resp.html.html

    def _cover_image_upload_and_get_url(self, url) -> str:
        matches = re.match(r".*/covers/(\d{4})/(\d{2})/(.*)", url)
        year = matches[1]
        month = matches[2]
        filename = matches[3]
        key = f"covers/{year}/{month}/{filename}"

        return self._upload(key, url)

    def _upload(self, key, url) -> str:
        """
        Check if key exists in the bucket.
        If not, then download it from url and upload it to S3 as key.
        Set the object ACL to public readable.
        Return the public URL for the object.

        Args:
            key (str): S3 object key
            url (str): source URL to download the data from

        Returns:
            (str): the public URL in S3
        """
        bucket = self.config["s3"]["bucket"]
        try:
            self.s3.head_object(Bucket=bucket, Key=key)
        except ClientError:
            resp = self._get(url)
            self.s3.upload_fileobj(
                BytesIO(resp.content),
                bucket,
                key,
                ExtraArgs={"StorageClass": "STANDARD_IA"},
            )

            resp = self.s3.put_object_acl(ACL="public-read", Bucket=bucket, Key=key)
            if resp is None:
                capture_message(f"Failed to set object ACL for {bucket}/{key}")

        return self.config["s3"]["object_url"].format(
            bucket=self.config["s3"]["bucket"],
            region=self.config["s3"]["region"],
            filekey=key,
        )

    def _thumbnail_small_image_upload_and_get_urls(self, urls) -> List:
        pub_urls = []
        for url in urls:
            matches = re.match(r".*/screenthumb/(\d{4})/(\d{2})/(.*)", url)
            year = matches[1]
            month = matches[2]
            filename = matches[3]
            key = f"screenthumbs/small/{year}/{month}/{filename}"
            pub_urls.append(self._upload(key, url))

        return pub_urls

    def _thumbnail_large_image_upload_and_get_urls(self, urls) -> List:
        pub_urls = []
        for url in urls:
            matches = re.match(r".*/screens/(\d{4})/(\d{2})/(.*)", url)
            year = matches[1]
            month = matches[2]
            filename = matches[3]
            key = f"screenthumbs/large/{year}/{month}/{filename}"
            pub_urls.append(self._upload(key, url))

        return pub_urls

    def _torrent_upload_and_get_url(self, url, torid, filename, publish_date) -> str:
        """

        Args:
            url (str): Source URL to torrent
            torid (str): Torrent ID
            filename (str): The filename to use in the S3 key
            publish_date (datetime): Torrent publish date

        Returns:
            (str) S3 public URL for the file
        """
        key = f"torrents/{publish_date.year}/{publish_date.month}/{filename}_{torid}.torrent"
        return self._upload(key, url)

    def signal_run(self):
        cloudwatch = self.aws_session.client(
            service_name="cloudwatch",
            region_name=self.config["secretsmanager"]["region"],
        )
        cloudwatch.put_metric_data(
            Namespace="Animetorrents",
            MetricData=[{"MetricName": "execution", "Value": 0.0}],
        )


if __name__ == "__main__":
    try:
        spider = Spider()
        spider.crawl()
    except RuntimeError as e:
        capture_exception(e)
    finally:
        logging.info("script end")
