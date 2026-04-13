"""Tests for email_sender/sender.py."""

import pytest

from email_sender.sender import render_digest_email


class TestRenderDigestEmail:
    def test_subject_contains_cw(self, sample_digest):
        subject, _, _ = render_digest_email(sample_digest)
        assert 'CW' in subject
        assert 'SWE AI Digest' in subject

    def test_subject_matches_period_not_send_date(self):
        """Regression: digest runs at midnight Monday (new week). Subject and page URL
        must reflect the period covered (period_end = Sunday CW15) not the send date
        (Monday = CW16), otherwise the email says 'CW16' but the page is 'CW15.html'."""
        digest = {
            # period_end is Sunday 2026-04-12 = ISO CW15
            'period_start': '2026-04-06T00:00:00+00:00',
            'period_end': '2026-04-12T23:59:59+00:00',
            'global_summary': 'Summary.',
            'articles': [],
        }
        subject, _, _ = render_digest_email(digest, feed_link='https://example.com/feed.xml')
        # CW15 ends on 2026-04-12; subject must say CW15, not CW16
        assert 'CW15' in subject, f"Expected CW15 in subject, got: {subject}"

    def test_page_url_matches_period_not_send_date(self):
        """Regression: the 'read online' link must point to the page for the period
        covered (2026-CW15.html), not the page for the send week (2026-CW16.html)."""
        digest = {
            'period_start': '2026-04-06T00:00:00+00:00',
            'period_end': '2026-04-12T23:59:59+00:00',
            'global_summary': 'Summary.',
            'articles': [],
        }
        _, plain, html = render_digest_email(digest, feed_link='https://example.com/feed.xml')
        assert '2026-CW15' in html, "Page URL in HTML should reference 2026-CW15"

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
        assert 'BOB' in plain


