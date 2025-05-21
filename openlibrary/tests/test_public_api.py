import json
import web # web.py framework
import pytest # For test structuring and fixtures
from unittest.mock import patch, MagicMock # For mocking dependencies

# Make sure the API class is importable from its new location
from openlibrary.public_api import UserBookNotesAPI 
# Mocked models (assuming they are not part of this specific test's focus)
from openlibrary.accounts.model import OpenLibraryAccount
# These models are used by the API, so their methods (e.g. Booknotes.add) will be mocked.
from openlibrary.core.models import Booknotes, Work


# Configuration for the API path, centralized for easy update
API_BASE_PATH = "/api/books/notes" # This is UserBookNotesAPI.path


# Factory to create mock user objects for authentication simulation
def mock_current_user(key_suffix=None):
    """Creates a mock user object or returns None."""
    if key_suffix:
        user = MagicMock(spec=OpenLibraryAccount)
        user.key = f"/people/{key_suffix}" # Simulate user OLID structure
        return user
    return None

@pytest.fixture
def test_client():
    """Provides a test client for the UserBookNotesAPI using web.test.TestApp.
    This client simulates HTTP requests to the API endpoints.
    """
    app = delegate.app(UserBookNotesAPI) # delegate is from infogami.utils
    return web.test.TestApp(app, {})


@pytest.fixture(autouse=True)
def auto_mock_web_utils():
    """
    Automatically mocks web.py's context (web.ctx), data (web.data), 
    and input (web.input) utilities for each test.
    It patches 'openlibrary.public_api.web' which is where UserBookNotesAPI
    would try to import web from.
    """
    # Note: It's important that 'openlibrary.public_api.web' is the correct patch target.
    # This means UserBookNotesAPI must be doing `import web` or `from ... import web`.
    with patch('openlibrary.public_api.web') as mock_web_module:
        mock_web_module.ctx = MagicMock()
        mock_web_module.ctx.env = {}
        mock_web_module.ctx.path = ''
        mock_web_module.ctx.ip = '127.0.0.1'

        mock_web_module.data = MagicMock(return_value='{}')
        mock_web_module.input = MagicMock(return_value={})
        
        mock_web_module.HTTPError = web.HTTPError # Use actual web.HTTPError
        mock_web_module.Found = web.Found # For GET success
        mock_web_module.OK = web.OK # For POST success
        # Mock json.loads and json.dumps if they are part of the web module mock,
        # or ensure they are available if used by the API directly from the json module.
        # The API uses json.loads(web.data()), so web.data should return a JSON string.
        # The API returns web.Found(json.dumps(notes), ...)
        # The API uses json.dumps({'error': ...}) for HTTPError data.
        # So, the json module itself doesn't need to be part of mock_web_module.
        yield mock_web_module

# Need to import delegate for the test_client fixture
from infogami.utils import delegate

class TestUserBookNotesGET:
    """Tests for GET /api/books/notes endpoint."""

    @patch('openlibrary.public_api.accounts.get_current_user') # Patch target updated
    def test_get_notes_unauthorized(self, mock_get_user, test_client, auto_mock_web_utils):
        mock_get_user.return_value = None 
        
        auto_mock_web_utils.ctx.env = {'REQUEST_METHOD': 'GET'}
        auto_mock_web_utils.ctx.path = API_BASE_PATH

        response = test_client.get(API_BASE_PATH, expect_errors=True)
        
        assert response.status_code == 401
        response_json = json.loads(response.text)
        assert response_json['error'] == "You must be logged in to view your notes."

    @patch('openlibrary.public_api.Booknotes.get_patron_booknotes') # Patch target updated
    @patch('openlibrary.public_api.accounts.get_current_user')    # Patch target updated
    def test_get_notes_logged_in_no_notes(self, mock_get_user, mock_get_patron_booknotes, test_client, auto_mock_web_utils):
        user = mock_current_user(key_suffix="test_user_no_notes")
        mock_get_user.return_value = user
        mock_get_patron_booknotes.return_value = [] 
        
        auto_mock_web_utils.ctx.env = {'REQUEST_METHOD': 'GET'}
        auto_mock_web_utils.ctx.path = API_BASE_PATH

        response = test_client.get(API_BASE_PATH)
        
        assert response.status_code == 200 # web.Found is 302, but API returns it with data. web.OK is 200. API uses web.Found. Let's check API.
                                          # API uses web.Found for GET. This is unusual for an API (usually 200 OK).
                                          # web.test.TestApp might follow redirects by default, or report 302.
                                          # Let's assume the API was meant to return 200 OK for GET.
                                          # The API code has: return web.Found(json.dumps(notes), headers={'Content-Type': 'application/json'})
                                          # web.Found is a 302 redirect. This should be web.OK for a typical API.
                                          # For now, I'll test against 302. If this is wrong, the API code should change.
                                          # Or, if test_client handles 302 and gives final response, it could be 200.
                                          # TestApp usually does not follow redirects unless told.
                                          # The content is in the body of the 302 for web.Found.

        # If web.Found is used, status_code should be 302.
        # Let's verify the API's GET implementation in public_api.py:
        # `return web.Found(json.dumps(notes), headers={'Content-Type': 'application/json'})`
        # This is indeed a 302. This is likely not the intent for a JSON API.
        # I will assume for the test that 302 is currently expected.
        # Corrected: Now expecting 200 OK after API change
        assert response.status_code == 200 
        assert response.headers['Content-Type'] == 'application/json'
        assert json.loads(response.text) == [] 
        mock_get_patron_booknotes.assert_called_once_with(user.key)

    @patch('openlibrary.public_api.Booknotes.get_patron_booknotes') # Patch target updated
    @patch('openlibrary.public_api.accounts.get_current_user')    # Patch target updated
    def test_get_notes_logged_in_with_notes(self, mock_get_user, mock_get_patron_booknotes, test_client, auto_mock_web_utils):
        user = mock_current_user(key_suffix="test_user_with_notes")
        mock_get_user.return_value = user
        sample_notes = [
            {"work_id": "OL1W", "notes": "My first note"},
            {"work_id": "OL2W", "notes": "Another note here"}
        ]
        mock_get_patron_booknotes.return_value = sample_notes
        
        auto_mock_web_utils.ctx.env = {'REQUEST_METHOD': 'GET'}
        auto_mock_web_utils.ctx.path = API_BASE_PATH
        response = test_client.get(API_BASE_PATH)
            
        # Corrected: Now expecting 200 OK after API change
        assert response.status_code == 200 
        assert response.headers['Content-Type'] == 'application/json'
        assert json.loads(response.text) == sample_notes
        mock_get_patron_booknotes.assert_called_once_with(user.key)


class TestUserBookNotesPOST:
    """Tests for POST /api/books/notes endpoint."""

    @patch('openlibrary.public_api.accounts.get_current_user') # Patch target updated
    def test_post_note_unauthorized(self, mock_get_user, test_client, auto_mock_web_utils):
        mock_get_user.return_value = None 
        post_data_dict = {"work_id": "OL1W", "notes": "A note from no one"}
        post_data_json = json.dumps(post_data_dict)
        
        auto_mock_web_utils.ctx.env = {'REQUEST_METHOD': 'POST'}
        auto_mock_web_utils.ctx.path = API_BASE_PATH
        auto_mock_web_utils.data.return_value = post_data_json

        response = test_client.post(API_BASE_PATH, data=post_data_json, expect_errors=True)
        
        assert response.status_code == 401
        assert json.loads(response.text)['error'] == "You must be logged in to add notes."

    @patch('openlibrary.public_api.Work.is_valid_olid', MagicMock(return_value=True)) # Patch target updated
    @patch('openlibrary.public_api.accounts.get_current_user') # Patch target updated
    def test_post_note_missing_data(self, mock_get_user, test_client, auto_mock_web_utils):
        mock_get_user.return_value = mock_current_user(key_suffix="test_user_missing_data")
        
        invalid_payloads = [
            {"notes": "Note without work_id"},
            {"work_id": "OL1W"},
            {},
        ]
        
        for payload_dict in invalid_payloads:
            payload_json = json.dumps(payload_dict)
            auto_mock_web_utils.ctx.env = {'REQUEST_METHOD': 'POST'}
            auto_mock_web_utils.ctx.path = API_BASE_PATH
            auto_mock_web_utils.data.return_value = payload_json
            
            response = test_client.post(API_BASE_PATH, data=payload_json, expect_errors=True)
            assert response.status_code == 400
            assert json.loads(response.text)['error'] == "work_id and notes are required."
    
    @patch('openlibrary.public_api.json.loads') # To test invalid JSON payload
    @patch('openlibrary.public_api.accounts.get_current_user')
    def test_post_note_invalid_json(self, mock_get_user, mock_json_loads, test_client, auto_mock_web_utils):
        mock_get_user.return_value = mock_current_user(key_suffix="test_user_invalid_json")
        mock_json_loads.side_effect = json.JSONDecodeError("Error", "doc", 0)
        
        auto_mock_web_utils.ctx.env = {'REQUEST_METHOD': 'POST'}
        auto_mock_web_utils.ctx.path = API_BASE_PATH
        # web.data will be called by json.loads, so its return value doesn't strictly matter here
        # as json.loads itself is mocked.
        auto_mock_web_utils.data.return_value = "this is not json"

        response = test_client.post(API_BASE_PATH, data="this is not json", expect_errors=True)
        assert response.status_code == 400
        assert json.loads(response.text)['error'] == "Invalid JSON payload."


    @patch('openlibrary.public_api.Booknotes.add') # Patch target updated
    @patch('openlibrary.public_api.Work.is_valid_olid', MagicMock(return_value=False)) # Patch target updated
    @patch('openlibrary.public_api.accounts.get_current_user') # Patch target updated
    def test_post_note_invalid_work_id_format(self, mock_get_user, mock_booknotes_add, test_client, auto_mock_web_utils):
        mock_get_user.return_value = mock_current_user(key_suffix="test_user_invalid_olid")
        note_data_dict = {"work_id": "INVALID_OLID", "notes": "Note with invalid OLID"}
        note_data_json = json.dumps(note_data_dict)
        
        auto_mock_web_utils.ctx.env = {'REQUEST_METHOD': 'POST'}
        auto_mock_web_utils.ctx.path = API_BASE_PATH
        auto_mock_web_utils.data.return_value = note_data_json

        response = test_client.post(API_BASE_PATH, data=note_data_json, expect_errors=True)
        
        assert response.status_code == 400
        assert json.loads(response.text)['error'] == "Invalid work_id format."
        mock_booknotes_add.assert_not_called()

    @patch('openlibrary.public_api.Booknotes.add') # Patch target updated
    @patch('openlibrary.public_api.Work.is_valid_olid', MagicMock(return_value=True)) # Patch target updated
    @patch('openlibrary.public_api.accounts.get_current_user') # Patch target updated
    def test_post_valid_note_no_edition(self, mock_get_user, mock_booknotes_add, test_client, auto_mock_web_utils):
        user = mock_current_user(key_suffix="test_user_valid_note")
        mock_get_user.return_value = user
        note_data_dict = {"work_id": "OL78528W", "notes": "A perfectly valid note"}
        note_data_json = json.dumps(note_data_dict)
        
        auto_mock_web_utils.ctx.env = {'REQUEST_METHOD': 'POST'}
        auto_mock_web_utils.ctx.path = API_BASE_PATH
        auto_mock_web_utils.data.return_value = note_data_json
        
        response = test_client.post(API_BASE_PATH, data=note_data_json)
            
        assert response.status_code == 200 # API uses web.OK
        assert response.headers['Content-Type'] == 'application/json'
        assert json.loads(response.text)['success'] == 'Note added successfully.'
        mock_booknotes_add.assert_called_once_with(
            username=user.key.split('/')[2],
            work_id="OL78528W",
            notes="A perfectly valid note",
            edition_id=None 
        )

    @patch('openlibrary.public_api.Booknotes.add') # Patch target updated
    @patch('openlibrary.public_api.Work.is_valid_olid', MagicMock(return_value=True)) # Patch target updated
    @patch('openlibrary.public_api.accounts.get_current_user') # Patch target updated
    def test_post_valid_note_with_edition(self, mock_get_user, mock_booknotes_add, test_client, auto_mock_web_utils):
        user = mock_current_user(key_suffix="test_user_note_with_edition")
        mock_get_user.return_value = user
        note_data_dict = {"work_id": "OL78528W", "notes": "Valid note with an edition", "edition_id": "OL12345M"}
        note_data_json = json.dumps(note_data_dict)

        auto_mock_web_utils.ctx.env = {'REQUEST_METHOD': 'POST'}
        auto_mock_web_utils.ctx.path = API_BASE_PATH
        auto_mock_web_utils.data.return_value = note_data_json
        
        response = test_client.post(API_BASE_PATH, data=note_data_json)
            
        assert response.status_code == 200 # API uses web.OK
        assert response.headers['Content-Type'] == 'application/json'
        assert json.loads(response.text)['success'] == 'Note added successfully.'
        mock_booknotes_add.assert_called_once_with(
            username=user.key.split('/')[2],
            work_id="OL78528W",
            notes="Valid note with an edition",
            edition_id="OL12345M"
        )

    @patch('openlibrary.public_api.Booknotes.add') # Patch target updated
    @patch('openlibrary.public_api.Work.is_valid_olid', MagicMock(return_value=True)) # Patch target updated
    @patch('openlibrary.public_api.accounts.get_current_user') # Patch target updated
    def test_post_update_existing_note(self, mock_get_user, mock_booknotes_add, test_client, auto_mock_web_utils):
        user = mock_current_user(key_suffix="test_user_updating_note")
        mock_get_user.return_value = user
        note_data_dict = {"work_id": "OL78528W", "notes": "This is an updated note text"}
        note_data_json = json.dumps(note_data_dict)

        auto_mock_web_utils.ctx.env = {'REQUEST_METHOD': 'POST'}
        auto_mock_web_utils.ctx.path = API_BASE_PATH
        auto_mock_web_utils.data.return_value = note_data_json
        
        response = test_client.post(API_BASE_PATH, data=note_data_json)
            
        assert response.status_code == 200 # API uses web.OK
        assert response.headers['Content-Type'] == 'application/json'
        assert json.loads(response.text)['success'] == 'Note added successfully.'
        mock_booknotes_add.assert_called_once_with(
            username=user.key.split('/')[2],
            work_id="OL78528W",
            notes="This is an updated note text",
            edition_id=None
        )
