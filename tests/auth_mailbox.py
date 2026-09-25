"""In-memory authentication mail adapter: test-only, never contacts SMTP."""
messages = {}


def send(recipient, code):
    messages[recipient.lower()] = code


def code_for(recipient):
    return messages[recipient.lower()]
