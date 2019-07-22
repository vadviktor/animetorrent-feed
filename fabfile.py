import os
from time import sleep
import pprint

from invoke import task
import boto3
from git import Repo
from git.exc import GitCommandError
import requests

_p = pprint.PrettyPrinter()


@task
def release(c, release):
    """
        tag current head with version passed in param

        instruct codebuild to build a new version

        when done instruct sentry of the new build release, version tag, included commit hashes
    """
    SENTRY_PROJECT_NAME = "animetorrents-feed"
    SENTRY_RELESE_TAG = f"animetorrents-feed@{release}"
    SENTRY_AUTH_TOKEN = (
        "82b53c310d4842a99950298a02431cf3104fb08b4e5a499ba16f8401ad9a3780"
    )
    AWS_REGION = "eu-west-1"
    AWS_BUILD_NAME = "anime-torrents"
    GIT_REPO = "github.com/vadviktor/animetorrent-feed"

    repo = Repo.init(path=os.getcwd())

    try:
        print(f"Creating git tag {release}")
        repo.create_tag(release)
    except GitCommandError as e:
        print("Skipping tag creation: {}".format(e.stderr))

    # git push tag
    try:
        print("Pushing tag to github")
        repo.remotes["github"].push()
        repo.remotes["github"].push(release)
        print("Pushing tag to aws")
        repo.remotes["aws"].push()
        repo.remotes["aws"].push(release)
    except GitCommandError as e:
        print("Can't push release: {}".format(e.stderr))
        exit(1)

    # codebuild
    aws_session = boto3.session.Session()
    codebuild = aws_session.client(service_name="codebuild", region_name=AWS_REGION)
    print("Start a new CodeBuild")
    response = codebuild.start_build(
        projectName=AWS_BUILD_NAME,
        environmentVariablesOverride=[
            {"name": "RELEASE_TAG", "value": SENTRY_RELESE_TAG, "type": "PLAINTEXT"}
        ],
    )
    build_id = response["build"]["id"]
    end_time = None
    status = None
    print("Waiting for the build to finish")
    while end_time is None:
        sleep(3)
        batch = codebuild.batch_get_builds(ids=[build_id])
        end_time, status = next(
            (
                (i.get("endTime", None), i.get("buildStatus", None))
                for i in batch["builds"]
                if i.get("id", None) == build_id
            ),
            None,
        )

    if status == "SUCCEEDED":
        print("Build finished")
    elif status in ["FAILED", "FAULT", "TIMED_OUT", "STOPPED"]:
        print(f"Build {status}")
        exit(1)

    # sentry

    print("Preparing Sentry release")
    previous_release_tag = repo.tags[-2].name
    previous_release_sha = next(
        (i.commit.hexsha for i in repo.tags if i.name == previous_release_tag), None
    )

    release_commits = []
    current_sha = repo.head.commit.hexsha
    release_commits.append(current_sha)
    while True:
        parent_sha = repo.commit(current_sha).parents[0].hexsha
        if parent_sha == previous_release_sha:
            break

        release_commits.append(parent_sha)
        current_sha = parent_sha

    data = {
        "commits": [{"id": c, "repository": GIT_REPO} for c in release_commits],
        "version": SENTRY_RELESE_TAG,
        "ref": current_sha,
        "projects": [SENTRY_PROJECT_NAME],
    }

    print("Creating Sentry release")
    resp = requests.post(
        "https://sentry.io/api/0/organizations/viktor-vad/releases/",
        headers={"Authorization": f"Bearer {SENTRY_AUTH_TOKEN}"},
        json=data,
    )
    print(resp.status_code, resp.reason)

    print("Creating Sentry deploy")
    resp = requests.post(
        f"https://sentry.io/api/0/organizations/viktor-vad/releases/{SENTRY_RELESE_TAG}/deploys/",
        headers={"Authorization": f"Bearer {SENTRY_AUTH_TOKEN}"},
        json={"environment": "production"},
    )
    print(resp.status_code, resp.reason)
