import re
from flask import Flask, flash, redirect, render_template, request, jsonify, url_for, session, request
from openai import OpenAI
import difflib
from langchain_community.chat_models import ChatOpenAI
from langchain.chains.summarize import load_summarize_chain
from dotenv import load_dotenv
import os
from youtube_transcript_api._errors import NoTranscriptFound
from authlib.integrations.flask_client import OAuth
import requests
import urllib
import logging
from datetime import datetime
# Load environment variables from a .env file
load_dotenv()

client = OpenAI()

app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY')  # Ensure this is set in your .env file for production
# oauth = OAuth(app)
ZOOM_OAUTH_AUTHORIZE_API = 'https://zoom.us/oauth/authorize?'
ZOOM_TOKEN_API = 'https://zoom.us/oauth/token'
# Configure logging
# logging.basicConfig(level=logging.DEBUG)
logging.basicConfig(filename='app_debug.log', level=logging.DEBUG,
                    format='%(asctime)s %(levelname)s %(name)s %(threadName)s : %(message)s')

# zoom = oauth.register(
#     name='zoom',
#     client_id=os.getenv("CLIENT_ID"),
#     client_secret=os.getenv("CLIENT_SECRET"),
#     authorize_url='https://zoom.us/oauth/authorize',
#     authorize_params=None,
#     access_token_url='https://zoom.us/oauth/token',
#     access_token_params=None,
#     refresh_token_url=None,
#     client_kwargs={'scope': 'user:read:admin recording:read:admin'}
# )

# print(zoom)
@app.route('/')
def index() -> str:
    """Render the index page."""
    return render_template('index.html')

@app.route('/zoom-login')
def login():
    # redirect_uri = url_for('authorize', _external=True)
    # return zoom.authorize_redirect(redirect_uri)
    if not session.get("token"):
        params = {
            'response_type': 'code',
            'client_id': os.getenv("CLIENT_ID"),
            'redirect_uri': os.getenv("ZOOM_REDIRECT_URI"),
            # 'state': 'your_state_value',  # Optional
            # 'code_challenge': 'your_code_challenge',  # Optional if using PKCE
            # 'code_challenge_method': 'S256',  # Optional if using PKCE
        }
        url = ZOOM_OAUTH_AUTHORIZE_API + urllib.parse.urlencode(params)
        return redirect(url)
    else:
        return redirect(url_for("recordings"))


@app.route('/authorize')
def get_token():
    # if not session["token"]:

    code = request.args.get('code')
    # get_token(code)
    # Note: In most cases, you'll want to store the access token, in, say,
    # a session for use in other parts of your web app.
    # return "Your user info is: %s" % get_username(access_token)
    # get_recordings()

    client_auth = requests.auth.HTTPBasicAuth(os.getenv("CLIENT_ID"), os.getenv("CLIENT_SECRET"))
    post_data = {"grant_type": "authorization_code",
                 "code": code,
                 "redirect_uri": os.getenv("ZOOM_REDIRECT_URI")}
    app.logger.debug(f'post data is {post_data}')
    token_response = requests.post("https://zoom.us/oauth/token",
                             auth=client_auth,
                             data=post_data)
    # token = response.json()

    print(token_response)
    if token_response.status_code != 200:
        
        return f"Failed to get token: {token_response.text}"
    
    try:
        token_json = token_response.json()
    except requests.exceptions.JSONDecodeError:
        return "Failed to decode token response"
    
    session["token"] = token_json
    # return token_json["access_token"]
    return redirect(url_for('recordings'))

# def get_recordings():

@app.route('/recordings')
def recordings():
    token = session.get('token')
    if token is None:
        return redirect(url_for('zoom-login'))
    app.logger.debug(f'token {token}')
    headers = {'Authorization': f'Bearer {token["access_token"]}'}
    app.logger.debug(f'token-access {token["access_token"]}')
    user_info = requests.get('https://api.zoom.us/v2/users/me', headers=headers)
    if user_info.status_code == 401:  # Unauthorized, try refreshing token
        access_token = refresh_token()
        headers['Authorization'] = f'Bearer {access_token}'
        user_info = requests.get('https://api.zoom.us/v2/users/me', headers=headers)
        
    print(user_info)
    user_info_json = user_info.json()
    app.logger.debug(f'user_info {user_info_json}')

    user_id = user_info_json['id']
    current_date = datetime.now().strftime('%Y-%m-%d')
    params = {
        'from': "2023-01-01",
        'to': current_date
    }
    recordings = requests.get(f'https://api.zoom.us/v2/users/{user_id}/recordings', headers=headers, params=params)
    recordings_json = recordings.json()
    app.logger.debug(f'recordings {recordings_json}')

    # recording_urls = []
    # for meeting in recordings_json.get('meetings', []):
    #     for recording_file in meeting.get('recording_files', []):
    #         if recording_file.get('play_url'):
    #             recording_urls.append(recording_file['play_url'])
    return render_template('recordings.html', meetings=recordings_json.get('meetings',[]))
    

def refresh_token():
    token = session.get("token")
    if not token or "refresh_token" not in token:
        return redirect(url_for('login'))

    client_auth = requests.auth.HTTPBasicAuth(os.getenv("CLIENT_ID"), os.getenv("CLIENT_SECRET"))
    post_data = {
        "grant_type": "refresh_token",
        "refresh_token": token["refresh_token"]
    }
    token_response = requests.post("https://zoom.us/oauth/token",
                                   auth=client_auth,
                                   data=post_data)

    if token_response.status_code != 200:
        return redirect(url_for('login'))

    try:
        token_json = token_response.json()
    except requests.exceptions.JSONDecodeError:
        return redirect(url_for('login'))

    session["token"] = token_json
    return token_json["access_token"]

@app.route('/transcript', methods=['GET'])
def display_transcript():
    recording_id = request.args.get('recording_id')
    if not recording_id:
        flash('No recording ID provided')
        return redirect(url_for('recordings'))
    
    token = session.get('token')
    if not token:
        return redirect(url_for('zoom-login'))
    
    headers = {'Authorization': f'Bearer {token["access_token"]}'}
    recordings = requests.get(f'https://api.zoom.us/v2/meetings/{recording_id}/recordings', headers=headers)
    
    if recordings.status_code != 200:
        return f"Failed to retrieve recording: {recordings.text}"
    
    recordings_json = recordings.json()
    
    transcript_file = None
    for file in recordings_json.get('recording_files', []):
        if file.get('file_type') == 'TRANSCRIPT':
            transcript_file = file
            break
    
    if not transcript_file:
        flash('No transcript found for this recording')
        return redirect(url_for('recordings'))
    
    transcript_url = transcript_file['download_url']
    transcript_response = requests.get(transcript_url, headers=headers)
    
    if transcript_response.status_code != 200:
        return f"Failed to retrieve transcript: {transcript_response.text}"
    
    transcript_content = transcript_response.content.decode('utf-8')
    proofread_content = proofread_transcript(transcript_content)
    
    return render_template('index.html', proofread=proofread_content, original=transcript_content)

@app.route('/upload', methods=['POST'])
def upload_file() -> str:
    """
    Handle file upload and process the transcript.

    Returns:
        str: Rendered HTML template with proofread content or redirection.
    """
    if 'transcript' not in request.files:
        flash('No file part')
        return redirect(request.url)
    
    file = request.files['transcript']
    
    if file.filename == '':
        flash('No selected file')
        return redirect(request.url)
    
    if file:
        try:
            content = file.read().decode('utf-8')
            proofread_content = proofread_transcript(content)
            diff_proofread_content = generate_inline_diff(content, proofread_content)
            return render_template('index.html', proofread=diff_proofread_content, original=content)
        except Exception as e:
            flash(f'An error occurred while processing the file: {e}')
            return redirect(request.url)

def generate_inline_diff(original: str, proofread: str) -> str:
    """
    Generate an HTML diff between the original and proofread transcripts.

    Args:
        original (str): Original transcript text.
        proofread (str): Proofread transcript text.

    Returns:
        str: HTML string with inline diff highlighting changes.
    """
    original_words = original.split()
    proofread_words = proofread.split()
    matcher = difflib.SequenceMatcher(None, original_words, proofread_words)
    diff_html = ""
    for opcode in matcher.get_opcodes():
        tag, i1, i2, j1, j2 = opcode
        if tag == 'equal':
            diff_html += ' ' + ' '.join(original_words[i1:i2])
        elif tag == 'replace':
            diff_html += ' ' + ''.join(f"<span class='diff-removed'>{word}</span>" for word in original_words[i1:i2])
            diff_html += ' ' + ''.join(f"<span class='diff-added'>{word}</span>" for word in proofread_words[j1:j2])
        elif tag == 'delete':
            diff_html += ' ' + ''.join(f"<span class='diff-removed'>{word}</span>" for word in original_words[i1:i2])
        elif tag == 'insert':
            diff_html += ' ' + ''.join(f"<span class='diff-added'>{word}</span>" for word in proofread_words[j1:j2])
    return diff_html

def proofread_transcript(transcript: str) -> str:
    """
    Proofread and correct a transcript using OpenAI's language model.

    Args:
        transcript (str): Transcript text to be proofread.

    Returns:
        str: Corrected and proofread transcript text.
    """
    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": "You are now an expert proofreader."},
            {"role": "user", "content": f"""I have an audio transcript of non-native English speaker. These transcripts may contain mispronounced words, unclear sentences, and grammatical errors due to language barriers. 
             My goal is to correct these errors and improve the overall clarity and coherence of the transcripts. 
             I would like your assistance in proofreading and editing these transcripts to make them sound natural, 
             grammatically correct, and easy to understand.For each transcript, please:\n 1.Identify and correct any grammatical errors.
            \n2.Correct any mispronounced words.
            \n3.Clarify any sentences that are unclear or awkwardly phrased.
            \n4.Ensure the overall readability and coherence of the text.
            \n5.Maintain the original meaning and context as much as possible.
            \n6. Just give the corrected text with grammar and punctuation, do not give out any extra text.:\n\n{transcript}"""}
        ],
        max_tokens=2048
    )
    return response.choices[0].message.content



if __name__ == '__main__':
    app.run(debug=True)
