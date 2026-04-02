from base64 import b64encode


def basic_auth_header(username: str, token: str) -> str:
    encoded_credentials = b64encode(f'{username}:{token}'.encode('utf-8')).decode('ascii')
    return f'Basic {encoded_credentials}'


def bitbucket_basic_auth_header(username: str, token: str) -> str:
    return basic_auth_header(username, token)
