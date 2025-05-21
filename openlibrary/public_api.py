import json
import web

from infogami.utils import delegate
from openlibrary import accounts
from openlibrary.core.models import Booknotes, Work # Added Work import


class UserBookNotesAPI(delegate.page):
    path = "/api/books/notes"

    def GET(self):
        user = accounts.get_current_user()
        if not user:
            raise web.HTTPError(
                "401 Unauthorized", {"Content-Type": "application/json"}, data='{"error": "You must be logged in to view your notes."}'
            )

        notes = Booknotes.get_patron_booknotes(user.key)
        # The original implementation returned delegate.RawText, 
        # web.py's delegate.app handles JSON conversion if content_type is application/json.
        # However, to be explicit and ensure correct JSON formatting, especially for errors,
        # it's better to handle JSON conversion directly.
        # For consistency with web.HTTPError data parameter, let's ensure this also returns JSON string.
        # Changed from web.Found to web.OK to return 200 status code.
        return web.OK(json.dumps(notes), headers={'Content-Type': 'application/json'})


    def POST(self):
        user = accounts.get_current_user()
        if not user:
            # Consistent error response format
            raise web.HTTPError(
                "401 Unauthorized", {"Content-Type": "application/json"}, data='{"error": "You must be logged in to add notes."}'
            )

        try:
            data = json.loads(web.data())
        except json.JSONDecodeError:
            raise web.HTTPError(
                "400 Bad Request", {"Content-Type": "application/json"}, data='{"error": "Invalid JSON payload."}'
            )
            
        work_id = data.get('work_id')
        notes_text = data.get('notes')
        edition_id = data.get('edition_id')  # Optional

        if not work_id or not notes_text:
            raise web.HTTPError(
                "400 Bad Request", {"Content-Type": "application/json"}, data='{"error": "work_id and notes are required."}'
            )

        if not Work.is_valid_olid(work_id):
            raise web.HTTPError(
                "400 Bad Request", {"Content-Type": "application/json"}, data='{"error": "Invalid work_id format."}'
            )
        
        # Assuming Booknotes.add can handle full work_id like "OL45804W"
        # and that it handles the database interaction correctly.
        Booknotes.add(
            username=user.key.split('/')[2],
            work_id=work_id, 
            notes=notes_text,
            edition_id=edition_id  # Pass it if provided, Booknotes.add should handle None
        )
        
        # Consistent success response format
        response_data = json.dumps({'success': 'Note added successfully.'})
        # Using web.Created or web.OK depending on desired semantics (POST usually 201 or 200 if updating)
        # Let's use 200 OK for simplicity, assuming it can be an update.
        return web.OK(response_data, headers={'Content-Type': 'application/json'})
