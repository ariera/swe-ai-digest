"""Tests for email_sender/sender.py."""

import pytest

from email_sender.sender import _escape, render_admin_notification, render_digest_email


class TestEscape:
    def test_ampersand(self):
        assert _escape('a & b') == 'a &amp; b'

    def test_angle_brackets(self):
        assert _escape('<script>') == '&lt;script&gt;'

    def test_quotes(self):
        assert _escape('"hello"') == '&quot;hello&quot;'

    def test_clean_string(self):
        assert _escape('hello world') == 'hello world'


class TestRenderDigestEmail:
    def test_subject_contains_cw(self, sample_digest):
        subject, _, _ = render_digest_email(sample_digest)
        assert 'CW' in subject
        assert 'SWE AI Digest' in subject

    def test_plain_text_has_global_summary(self, sample_digest):
        _, plain, _ = render_digest_email(sample_digest)
        assert 'engineers discussed AI coding tools' in plain

    def test_plain_text_has_author(self, sample_digest):
        _, plain, _ = render_digest_email(sample_digest)
        assert 'ALICE ENGINEER' in plain

    def test_plain_text_has_bio(self, sample_digest):
        _, plain, _ = render_digest_email(sample_digest)
        assert 'A great engineer.' in plain

    def test_plain_text_has_article_link(self, sample_digest):
        _, plain, _ = render_digest_email(sample_digest)
        assert 'https://alice.example.com/ai-coding' in plain

    def test_html_has_global_summary(self, sample_digest):
        _, _, html = render_digest_email(sample_digest)
        assert 'engineers discussed AI coding tools' in html

    def test_html_has_author_heading(self, sample_digest):
        _, _, html = render_digest_email(sample_digest)
        assert 'Alice Engineer' in html

    def test_html_is_valid_structure(self, sample_digest):
        _, _, html = render_digest_email(sample_digest)
        assert '<html>' in html
        assert '</html>' in html
        assert '<body' in html

    def test_multiple_authors_grouped(self):
        digest = {
            'generated_at': '2026-04-04T10:00:00+00:00',
            'period_start': '2026-03-28T00:00:00+00:00',
            'period_end': '2026-04-04T00:00:00+00:00',
            'global_summary': 'Global summary.',
            'stats': {},
            'articles': [
                {'author': 'Alice', 'bio': 'Bio A', 'title': 'Post 1',
                 'url': 'http://a.com/1', 'published_at': '2026-04-01', 'summary': 'S1'},
                {'author': 'Alice', 'bio': 'Bio A', 'title': 'Post 2',
                 'url': 'http://a.com/2', 'published_at': '2026-04-02', 'summary': 'S2'},
                {'author': 'Bob', 'bio': 'Bio B', 'title': 'Post 3',
                 'url': 'http://b.com/1', 'published_at': '2026-04-01', 'summary': 'S3'},
            ],
        }
        _, plain, _ = render_digest_email(digest)
        # Alice's bio should appear once
        assert plain.count('Bio A') == 1
        assert 'Post 1' in plain
        assert 'Post 2' in plain
        assert 'Bob' in plain


class TestRenderAdminNotification:
    def test_reason_in_body(self):
        subject, body = render_admin_notification('No articles found', 'Details here')
        assert 'No articles found' in body
        assert 'Details here' in body

    def test_subject(self):
        subject, _ = render_admin_notification('test')
        assert 'SWE AI Digest' in subject
