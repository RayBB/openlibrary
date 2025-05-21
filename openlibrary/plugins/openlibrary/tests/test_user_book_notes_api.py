import json
import web # web.py framework
import pytest # For test structuring and fixtures
from unittest.mock import patch, MagicMock # For mocking dependencies

# Make sure the API class is importable
from openlibrary.plugins.openlibrary.api import UserBookNotesAPI
# Mocked models (assuming they are not part of this specific test's focus)
from openlibrary.accounts.model import OpenLibraryAccount

# Configuration for the API path, centralized for easy update
API_BASE_PATH = "/api/books/notes"


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
    # Ensure the UserBookNotesAPI.path is correctly set for delegate routing if necessary
    # For web.test.TestApp, the app instance itself is what matters.
    # If UserBookNotesAPI.path is used internally by delegate.app for routing, ensure it's correct.
    # UserBookNotesAPI.path = API_BASE_PATH # This might be needed if delegate uses it dynamically
    
    # We need to pass the application object (delegate.app instance) to TestApp
    # delegate.app(...) is the WSGI application
    app = delegate.app(UserBookNotesAPI)
    return web.test.TestApp(app, {})


@pytest.fixture(autouse=True)
def auto_mock_web_utils():
    """
    Automatically mocks web.py's context (web.ctx), data (web.data), 
    and input (web.input) utilities for each test. This ensures a clean,
    controlled environment for web interactions, preventing state leakage
    and allowing tests to define web utility behavior as needed.
    
    It patches 'openlibrary.plugins.openlibrary.api.web' which is where UserBookNotesAPI
    would try to import web from.
    """
    with patch('openlibrary.plugins.openlibrary.api.web') as mock_web_module:
        # Setup mock web.ctx with common attributes
        mock_web_module.ctx = MagicMock()
        mock_web_module.ctx.env = {}  # For storing request environment like METHOD
        mock_web_module.ctx.path = '' # For storing request path
        mock_web_module.ctx.ip = '127.0.0.1' # Default IP

        # Default mock for web.data() used in POST/PUT requests
        # This will be referenced as `web.data()` in the API code.
        mock_web_module.data = MagicMock(return_value='{}')
        
        # Default mock for web.input() used for GET query parameters
        mock_web_module.input = MagicMock(return_value={})
        
        # Ensure web.HTTPError is the actual web.py HTTPError for type checking
        mock_web_module.HTTPError = web.HTTPError
        
        # If UserBookNotesAPI uses web.json_output, mock it appropriately
        # The current API uses delegate.RawText, so direct mocking of web.json_output might not be needed
        # for the API's own code, but good to have if delegate layer uses it.
        mock_web_module.json_output = lambda data_dict: delegate.RawText(json.dumps(data_dict), content_type="application/json")
        
        yield mock_web_module


class TestUserBookNotesGET:
    """Tests for GET /api/books/notes endpoint."""

    @patch('openlibrary.plugins.openlibrary.api.accounts.get_current_user')
    def test_get_notes_unauthorized(self, mock_get_user, test_client, auto_mock_web_utils):
        mock_get_user.return_value = None # Simulate no user logged in
        
        # Set up the environment for the request via auto_mock_web_utils if needed by API internals
        auto_mock_web_utils.ctx.env = {'REQUEST_METHOD': 'GET'}
        auto_mock_web_utils.ctx.path = API_BASE_PATH

        response = test_client.get(API_BASE_PATH, expect_errors=True)
        
        assert response.status_code == 401
        response_json = json.loads(response.text) # Use .text for web.test.TestResponse
        assert response_json['error'] == "You must be logged in to view your notes."

    @patch('openlibrary.plugins.openlibrary.api.Booknotes.get_patron_booknotes')
    @patch('openlibrary.plugins.openlibrary.api.accounts.get_current_user')
    def test_get_notes_logged_in_no_notes(self, mock_get_user, mock_get_patron_booknotes, test_client, auto_mock_web_utils):
        user = mock_current_user(key_suffix="test_user_no_notes")
        mock_get_user.return_value = user
        mock_get_patron_booknotes.return_value = [] # Simulate user has no notes
        
        auto_mock_web_utils.ctx.env = {'REQUEST_METHOD': 'GET'}
        auto_mock_web_utils.ctx.path = API_BASE_PATH

        response = test_client.get(API_BASE_PATH)
        
        assert response.status_code == 200
        assert response.headers['Content-Type'] == 'application/json'
        assert json.loads(response.text) == []
        mock_get_patron_booknotes.assert_called_once_with(user.key)

    @patch('openlibrary.plugins.openlibrary.api.Booknotes.get_patron_booknotes')
    @patch('openlibrary.plugins.openlibrary.api.accounts.get_current_user')
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
            
        assert response.status_code == 200
        assert response.headers['Content-Type'] == 'application/json'
        assert json.loads(response.text) == sample_notes
        mock_get_patron_booknotes.assert_called_once_with(user.key)


class TestUserBookNotesPOST:
    """Tests for POST /api/books/notes endpoint."""

    @patch('openlibrary.plugins.openlibrary.api.accounts.get_current_user')
    def test_post_note_unauthorized(self, mock_get_user, test_client, auto_mock_web_utils):
        mock_get_user.return_value = None 
        post_data_dict = {"work_id": "OL1W", "notes": "A note from no one"}
        post_data_json = json.dumps(post_data_dict)
        
        auto_mock_web_utils.ctx.env = {'REQUEST_METHOD': 'POST'}
        auto_mock_web_utils.ctx.path = API_BASE_PATH
        auto_mock_web_utils.data.return_value = post_data_json # Set what web.data() will return

        response = test_client.post(API_BASE_PATH, data=post_data_json, expect_errors=True)
        
        assert response.status_code == 401
        assert json.loads(response.text)['error'] == "You must be logged in to add notes."

    @patch('openlibrary.plugins.openlibrary.api.Work.is_valid_olid', MagicMock(return_value=True))
    @patch('openlibrary.plugins.openlibrary.api.accounts.get_current_user')
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

    @patch('openlibrary.plugins.openlibrary.api.Booknotes.add') 
    @patch('openlibrary.plugins.openlibrary.api.Work.is_valid_olid', MagicMock(return_value=False)) 
    @patch('openlibrary.plugins.openlibrary.api.accounts.get_current_user')
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

    @patch('openlibrary.plugins.openlibrary.api.Booknotes.add')
    @patch('openlibrary.plugins.openlibrary.api.Work.is_valid_olid', MagicMock(return_value=True))
    @patch('openlibrary.plugins.openlibrary.api.accounts.get_current_user')
    def test_post_valid_note_no_edition(self, mock_get_user, mock_booknotes_add, test_client, auto_mock_web_utils):
        user = mock_current_user(key_suffix="test_user_valid_note")
        mock_get_user.return_value = user
        note_data_dict = {"work_id": "OL78528W", "notes": "A perfectly valid note"}
        note_data_json = json.dumps(note_data_dict)
        
        auto_mock_web_utils.ctx.env = {'REQUEST_METHOD': 'POST'}
        auto_mock_web_utils.ctx.path = API_BASE_PATH
        auto_mock_web_utils.data.return_value = note_data_json
        
        response = test_client.post(API_BASE_PATH, data=note_data_json)
            
        assert response.status_code == 200
        assert json.loads(response.text)['success'] == 'Note added successfully.'
        mock_booknotes_add.assert_called_once_with(
            username=user.key.split('/')[2],
            work_id="OL78528W",
            notes="A perfectly valid note",
            edition_id=None 
        )

    @patch('openlibrary.plugins.openlibrary.api.Booknotes.add')
    @patch('openlibrary.plugins.openlibrary.api.Work.is_valid_olid', MagicMock(return_value=True))
    @patch('openlibrary.plugins.openlibrary.api.accounts.get_current_user')
    def test_post_valid_note_with_edition(self, mock_get_user, mock_booknotes_add, test_client, auto_mock_web_utils):
        user = mock_current_user(key_suffix="test_user_note_with_edition")
        mock_get_user.return_value = user
        note_data_dict = {"work_id": "OL78528W", "notes": "Valid note with an edition", "edition_id": "OL12345M"}
        note_data_json = json.dumps(note_data_dict)

        auto_mock_web_utils.ctx.env = {'REQUEST_METHOD': 'POST'}
        auto_mock_web_utils.ctx.path = API_BASE_PATH
        auto_mock_web_utils.data.return_value = note_data_json
        
        response = test_client.post(API_BASE_PATH, data=note_data_json)
            
        assert response.status_code == 200
        assert json.loads(response.text)['success'] == 'Note added successfully.'
        mock_booknotes_add.assert_called_once_with(
            username=user.key.split('/')[2],
            work_id="OL78528W",
            notes="Valid note with an edition",
            edition_id="OL12345M"
        )

    @patch('openlibrary.plugins.openlibrary.api.Booknotes.add')
    @patch('openlibrary.plugins.openlibrary.api.Work.is_valid_olid', MagicMock(return_value=True))
    @patch('openlibrary.plugins.openlibrary.api.accounts.get_current_user')
    def test_post_update_existing_note(self, mock_get_user, mock_booknotes_add, test_client, auto_mock_web_utils):
        user = mock_current_user(key_suffix="test_user_updating_note")
        mock_get_user.return_value = user
        note_data_dict = {"work_id": "OL78528W", "notes": "This is an updated note text"}
        note_data_json = json.dumps(note_data_dict)

        auto_mock_web_utils.ctx.env = {'REQUEST_METHOD': 'POST'}
        auto_mock_web_utils.ctx.path = API_BASE_PATH
        auto_mock_web_utils.data.return_value = note_data_json
        
        response = test_client.post(API_BASE_PATH, data=note_data_json)
            
        assert response.status_code == 200
        assert json.loads(response.text)['success'] == 'Note added successfully.'
        mock_booknotes_add.assert_called_once_with(
            username=user.key.split('/')[2],
            work_id="OL78528W",
            notes="This is an updated note text",
            edition_id=None
        )
