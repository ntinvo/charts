# -*- coding: utf-8 -*-
"""Report only submission

Partners or redhat associates can publish their chart by submitting
error-free report that was generated by chart-verifier.
"""
import os
import json
import base64
import pathlib
import logging
from tempfile import TemporaryDirectory
from dataclasses import dataclass
from string import Template

import git
import yaml
import pytest
from pytest_bdd import (
    given,
    scenario,
    then,
    when,
)

from functional.utils import *

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


@pytest.fixture
def secrets():
    @dataclass
    class Secret:
        test_repo: str
        bot_name: str
        bot_token: str
        base_branch: str
        pr_branch: str

        pr_number: int = -1
        vendor_type: str = ''
        vendor: str = ''
        owners_file_content: str = """\
chart:
  name: ${chart_name}
  shortDescription: Test chart for testing chart submission workflows.
publicPgpKey: null
users:
- githubUsername: ${bot_name}
vendor:
  label: ${vendor}
  name: ${vendor}
"""
        test_report: str = 'tests/data/report.yaml'
        chart_name, chart_version = get_name_and_version_from_report(
            test_report)

    bot_name, bot_token = get_bot_name_and_token()

    test_repo = TEST_REPO
    repo = git.Repo()

    # Differentiate between github runner env and local env
    github_actions = os.environ.get("GITHUB_ACTIONS")
    if github_actions:
        # Create a new branch locally from detached HEAD
        head_sha = repo.git.rev_parse('--short', 'HEAD')
        local_branches = [h.name for h in repo.heads]
        if head_sha not in local_branches:
            repo.git.checkout('-b', f'{head_sha}')

    current_branch = repo.active_branch.name
    r = github_api(
        'get', f'repos/{test_repo}/branches', bot_token)
    branches = json.loads(r.text)
    branch_names = [branch['name'] for branch in branches]
    if current_branch not in branch_names:
        logger.info(
            f"{test_repo}:{current_branch} does not exists, creating with local branch")
    repo.git.push(f'https://x-access-token:{bot_token}@github.com/{test_repo}',
                  f'HEAD:refs/heads/{current_branch}', '-f')

    base_branch = f'report-only-{current_branch}'
    pr_branch = base_branch + '-pr'

    secrets = Secret(test_repo, bot_name, bot_token, base_branch, pr_branch)
    yield secrets

    # Teardown step to cleanup branches
    repo.git.worktree('prune')

    if github_actions:
        logger.info(f"Delete remote '{head_sha}' branch")
        github_api(
            'delete', f'repos/{secrets.test_repo}/git/refs/heads/{head_sha}', secrets.bot_token)

    logger.info(f"Delete '{secrets.test_repo}:{secrets.base_branch}'")
    github_api(
        'delete', f'repos/{secrets.test_repo}/git/refs/heads/{secrets.base_branch}', secrets.bot_token)

    logger.info(f"Delete '{secrets.test_repo}:{secrets.base_branch}-gh-pages'")
    github_api(
        'delete', f'repos/{secrets.test_repo}/git/refs/heads/{secrets.base_branch}-gh-pages', secrets.bot_token)

    logger.info(f"Delete '{secrets.test_repo}:{secrets.pr_branch}'")
    github_api(
        'delete', f'repos/{secrets.test_repo}/git/refs/heads/{secrets.pr_branch}', secrets.bot_token)

    logger.info(f"Delete local '{secrets.base_branch}'")
    try:
        repo.git.branch('-D', secrets.base_branch)
    except git.exc.GitCommandError:
        logger.info(f"Local '{secrets.base_branch}' does not exist")


@scenario('features/report_without_chart.feature', "The partner hashicorp submits a error-free report for the vault chart")
def test_partner_submits_report_without_any_errors():
    """The partner hashicorp submits a error-free report for the vault chart."""


@scenario('features/report_without_chart.feature', "The redhat associate submits a error-free report for the vault chart")
def test_redhat_submits_report_without_any_errors():
    """The redhat associate submits a error-free report for the vault chart."""


@given("hashicorp is a valid partner")
def hashicorp_is_a_valid_partner(secrets):
    """hashicorp is a valid partner"""
    secrets.vendor_type = 'partners'
    secrets.vendor = get_unique_vendor('hashicorp')


@given("a redhat associate has a valid identity")
def redhat_associate_is_valid(secrets):
    """a redhat associate has a valid identity"""
    secrets.vendor_type = 'redhat'
    secrets.vendor = get_unique_vendor('redhat')


@given("hashicorp has created a error-free report")
@given("the redhat associate has created a report without any errors")
def the_user_has_created_a_report_without_errors(secrets):
    """The user has created a report without any errors."""

    with TemporaryDirectory(prefix='tci-') as temp_dir:
        secrets.base_branch = f'{secrets.vendor_type}-{secrets.vendor}-{secrets.base_branch}'
        secrets.pr_branch = f'{secrets.base_branch}-pr'

        repo = git.Repo(os.getcwd())
        set_git_username_email(repo, secrets.bot_name, GITHUB_ACTIONS_BOT_EMAIL)
        if os.environ.get('WORKFLOW_DEVELOPMENT'):
            logger.info("Wokflow development enabled")
            repo.git.add(A=True)
            repo.git.commit('-m', 'Checkpoint')

        # Get SHA from 'dev-gh-pages' branch
        logger.info(
            f"Create '{secrets.test_repo}:{secrets.base_branch}-gh-pages' from '{secrets.test_repo}:dev-gh-pages'")
        r = github_api(
            'get', f'repos/{secrets.test_repo}/git/ref/heads/dev-gh-pages', secrets.bot_token)
        j = json.loads(r.text)
        sha = j['object']['sha']

        # Create a new gh-pages branch for testing
        data = {'ref': f'refs/heads/{secrets.base_branch}-gh-pages', 'sha': sha}
        r = github_api(
            'post', f'repos/{secrets.test_repo}/git/refs', secrets.bot_token, json=data)

        # Make PR's from a temporary directory
        old_cwd = os.getcwd()
        logger.info(f'Worktree directory: {temp_dir}')
        repo.git.worktree('add', '--detach', temp_dir, f'HEAD')

        os.chdir(temp_dir)
        repo = git.Repo(temp_dir)
        set_git_username_email(repo, secrets.bot_name, GITHUB_ACTIONS_BOT_EMAIL)
        repo.git.checkout('-b', secrets.base_branch)
        chart_dir = f'charts/{secrets.vendor_type}/{secrets.vendor}/{secrets.chart_name}'
        pathlib.Path(
            f'{chart_dir}/{secrets.chart_version}').mkdir(parents=True, exist_ok=True)

        # Remove chart files from base branch
        logger.info(
            f"Remove {chart_dir}/{secrets.chart_version} from {secrets.test_repo}:{secrets.base_branch}")
        try:
            repo.git.rm('-rf', '--cached', f'{chart_dir}/{secrets.chart_version}')
            repo.git.commit(
                '-m', f'Remove {chart_dir}/{secrets.chart_version}')
            repo.git.push(f'https://x-access-token:{secrets.bot_token}@github.com/{secrets.test_repo}',
                            f'HEAD:refs/heads/{secrets.base_branch}')
        except git.exc.GitCommandError:
            logger.info(
                f"{chart_dir}/{secrets.chart_version} not exist on {secrets.test_repo}:{secrets.base_branch}")

        # Remove the OWNERS file from base branch
        logger.info(
            f"Remove {chart_dir}/OWNERS from {secrets.test_repo}:{secrets.base_branch}")
        try:
            repo.git.rm('-rf', '--cached', f'{chart_dir}/OWNERS')
            repo.git.commit(
                '-m', f'Remove {chart_dir}/OWNERS')
            repo.git.push(f'https://x-access-token:{secrets.bot_token}@github.com/{secrets.test_repo}',
                            f'HEAD:refs/heads/{secrets.base_branch}')
        except git.exc.GitCommandError:
            logger.info(
                f"{chart_dir}/OWNERS not exist on {secrets.test_repo}:{secrets.base_branch}")

        # Create the OWNERS file from the string template
        values = {'bot_name': secrets.bot_name,
                  'vendor': secrets.vendor, 'chart_name': secrets.chart_name}
        content = Template(secrets.owners_file_content).substitute(values)
        with open(f'{chart_dir}/OWNERS', 'w') as fd:
            fd.write(content)

        # Push OWNERS file to the test_repo
        logger.info(
            f"Push OWNERS file to '{secrets.test_repo}:{secrets.base_branch}'")
        repo.git.add(f'{chart_dir}/OWNERS')
        repo.git.commit(
            '-m', f"Add {secrets.vendor} {secrets.chart_name} OWNERS file")
        repo.git.push(f'https://x-access-token:{secrets.bot_token}@github.com/{secrets.test_repo}',
                      f'HEAD:refs/heads/{secrets.base_branch}', '-f')

        # Copy report to temporary location and push to test_repo:pr_branch
        logger.info(
            f"Push report to '{secrets.test_repo}:{secrets.pr_branch}'")
        tmpl = open(secrets.test_report).read()
        values = {'repository': secrets.test_repo,
                  'branch': secrets.base_branch}
        content = Template(tmpl).substitute(values)
        with open(f'{chart_dir}/{secrets.chart_version}/report.yaml', 'w') as fd:
            fd.write(content)

        repo.git.add(f'{chart_dir}/{secrets.chart_version}/report.yaml')
        repo.git.commit(
            '-m', f"Add {secrets.vendor} {secrets.chart_name} {secrets.chart_version} report")

        repo.git.push(f'https://x-access-token:{secrets.bot_token}@github.com/{secrets.test_repo}',
                      f'HEAD:refs/heads/{secrets.pr_branch}', '-f')

        os.chdir(old_cwd)


@when("hashicorp sends a pull request with the report")
@when("the redhat associate sends the pull request with the report")
def the_user_sends_the_pull_request_with_the_report(secrets):
    """The user sends the pull request with the report."""
    data = {'head': secrets.pr_branch, 'base': secrets.base_branch,
            'title': secrets.pr_branch, 'body': os.environ.get('PR_BODY')}

    logger.info(
        f"Create PR with report from '{secrets.test_repo}:{secrets.pr_branch}'")
    r = github_api(
        'post', f'repos/{secrets.test_repo}/pulls', secrets.bot_token, json=data)
    j = json.loads(r.text)
    secrets.pr_number = j['number']


@then("hashicorp sees the pull request is merged")
@then("the redhat associate sees the pull request is merged")
def the_user_should_see_the_pull_request_getting_merged(secrets):
    """The user should see the pull request getting merged."""

    run_id = get_run_id(secrets)
    conclusion = get_run_result(secrets, run_id)
    if conclusion == 'success':
        logger.info("Workflow run was 'success'")
    else:
        pytest.fail(
            f"Workflow for the submitted PR did not success, run id: {run_id}")

    r = github_api(
        'get', f'repos/{secrets.test_repo}/pulls/{secrets.pr_number}/merge', secrets.bot_token)
    if r.status_code == 204:
        logger.info("PR merged sucessfully")
    else:
        pytest.fail("Workflow for submitted PR success but PR not merged")


@then("the index.yaml file is updated with an entry for the submitted chart")
def the_index_yaml_is_updated_with_a_new_entry(secrets):
    """The index.yaml file is updated with a new entry."""

    repo = git.Repo(os.getcwd())
    old_branch = repo.active_branch.name
    repo.git.fetch(f'https://github.com/{secrets.test_repo}.git',
                   '{0}:{0}'.format(f'{secrets.base_branch}-gh-pages'))
    repo.git.checkout(f'{secrets.base_branch}-gh-pages')
    with open('index.yaml', 'r') as fd:
        try:
            index = yaml.safe_load(fd)
        except yaml.YAMLError as err:
            pytest.fail(f"error parsing index.yaml: {err}")

    entry = secrets.vendor + '-' + secrets.chart_name
    if entry not in index['entries']:
        pytest.fail(
            f"{secrets.chart_name} {secrets.chart_version} not added to index")

    version_list = [release['version'] for release in index['entries'][entry]]
    if secrets.chart_version not in version_list:
        pytest.fail(
            f"{secrets.chart_name} {secrets.chart_version} not added to index")

    logger.info("Index updated correctly, cleaning up local branch")
    repo.git.checkout(old_branch)
    repo.git.branch('-D', f'{secrets.base_branch}-gh-pages')
