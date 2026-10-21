"""Original file bytes and parser manifests stay in the current tenant database."""
import json
import sqlite3

from .document_parser import parse_document


class FileSources:
    def __init__(self, library):
        self.library, self.store = library, library.store
        schema = '''
        CREATE TABLE IF NOT EXISTS library_files(
            revision_id TEXT PRIMARY KEY REFERENCES library_revisions(id) ON DELETE CASCADE,
            filename TEXT NOT NULL,file_sha TEXT NOT NULL,data BLOB NOT NULL,manifest_json TEXT NOT NULL);
        CREATE TRIGGER IF NOT EXISTS library_file_immutable BEFORE UPDATE ON library_files
        BEGIN SELECT RAISE(ABORT,'original files are immutable'); END;
        '''
        with self.store.memory_transaction():
            statement = ''
            for line in schema.splitlines(keepends=True):
                statement += line
                if sqlite3.complete_statement(statement):
                    self.store.db.execute(statement); statement = ''

    def ingest(self, filename, data, *, title='', kind='document', authorship='unknown',
               scope='personal', session_id=None, encoding='auto',source_time=None):
        from .library import SourceInput
        parsed = parse_document(filename, data, encoding)
        body = SourceInput(kind=kind, title=title or parsed.filename, content=parsed.content,
            authorship=authorship, scope=scope, session_id=session_id, origin=parsed.filename,source_time=source_time)
        with self.store.memory_transaction():
            source = self.library.create(body)
            self.store.db.execute('INSERT INTO library_files VALUES(?,?,?,?,?)', (
                source['revision']['id'], parsed.filename, parsed.manifest['file_sha256'], data,
                json.dumps(parsed.manifest, ensure_ascii=False)))
            return self.library.detail(source['id'])

    def manifest(self, revision_id):
        row = self.store.db.execute('SELECT filename,manifest_json FROM library_files WHERE revision_id=?', (revision_id,)).fetchone()
        return {'filename': row['filename'], **json.loads(row['manifest_json'])} if row else None

    def locations(self, revision_id, start, end):
        manifest = self.manifest(revision_id)
        if not manifest: return None
        return {'filename': manifest['filename'], 'format': manifest['format'],
            'file_sha256': manifest['file_sha256'], 'locations': [a['locator'] for a in manifest['anchors']
                if a['start'] < end and a['end'] > start][:8]}

    def original(self, source_id, revision_id=None):
        from .library import LibraryError
        with self.store.lock:
            source = self.library._source(source_id)
            revision = self.library._revision(revision_id or source['head_id'])
            if revision['source_id'] != source_id:
                raise LibraryError(404, '文件版本不属于该资料')
            row = self.store.db.execute('SELECT filename,data,file_sha FROM library_files WHERE revision_id=?',
                                        (revision['id'],)).fetchone()
            if not row: raise LibraryError(404, '此版本没有上传的原始文件')
            import hashlib
            if hashlib.sha256(row['data']).hexdigest() != row['file_sha']:
                raise LibraryError(409, '原始文件指纹失效')
            return row['filename'], row['data']
