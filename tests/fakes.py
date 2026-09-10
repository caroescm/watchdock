"""Duck-typed stand-ins for the PyGithub objects the pipeline touches.
No real API calls anywhere in the suite.

Keyword arguments inject the failure a test wants to provoke.
"""
from types import SimpleNamespace


class FakeComment:
    def __init__(self, body):
        self.body = body

    def edit(self, body):
        self.body = body


class FakeContentFile:
    def __init__(self, sha="fake-sha-123", content=b""):
        self.sha = sha
        self.decoded_content = content


class FakePR:
    """Enough of a PullRequest for reporting, suggestions and commits."""
    number = 7
    title = "A PR"
    head = SimpleNamespace(ref="some-branch", sha="fake-head-sha")

    def __init__(self, review_comment_error=None, existing_comments=None):
        self.comments = list(existing_comments or [])   # FakeComment objects, in order
        self.review_comments = []
        self._review_comment_error = review_comment_error

    @property
    def issue_comments(self):
        """Bodies of the plain PR comments posted so far."""
        return [c.body for c in self.comments]

    def get_issue_comments(self):
        return list(self.comments)

    def create_issue_comment(self, body):
        self.comments.append(FakeComment(body))

    def create_review_comment(self, body, commit, path, line):
        if self._review_comment_error:
            raise self._review_comment_error
        self.review_comments.append({"body": body, "commit": commit, "path": path, "line": line})


class FakeRepo:
    """Enough of a Repository for reading a file and committing one."""

    def __init__(self, update_error=None, contents=None):
        self.updates = []           # every update_file call, in order
        self._update_error = update_error
        self._contents = contents or {}

    @property
    def updated(self):
        return self.updates[-1] if self.updates else None

    @property
    def update_calls(self):
        return len(self.updates)

    def get_contents(self, path, ref):
        return FakeContentFile(content=self._contents.get(path, b""))

    def update_file(self, path, message, content, sha, branch):
        if self._update_error:
            raise self._update_error
        self.updates.append({"path": path, "message": message, "content": content, "sha": sha, "branch": branch})
