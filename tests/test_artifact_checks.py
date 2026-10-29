import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace

from journey.artifact_checks import (check_ooxml_artifact, check_text_artifact, office_paragraphs, xlsx_first_cell,
                                     check_pdf_artifact, check_text_artifacts)
from journey.catalog import load_case
from journey.client import Failure
from journey.outcomes import assess


class ArtifactChecksTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.inventory = self.root / 'artifacts' / 'conversation' / 'inventory.json'
        self.inventory.parent.mkdir(parents=True)
        self.execution = SimpleNamespace(evidence=SimpleNamespace(root=self.root), report={},
                                         collect_artifacts=lambda cid: None, checkpoint=lambda: None)

    def run_check(self, entries, files=None, filename='result.txt'):
        self.inventory.write_text(json.dumps(entries))
        for name, content in (files or {}).items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        checks = []
        delivered = check_text_artifact(self.execution, 'conversation', 'task', filename,
                                        'unique-content', checks, 'artifact')
        return delivered, checks

    def item(self, task='task', name='result.txt', path='download/result.txt'):
        return {'task_id': task, 'file_name': name, 'local_path': path}

    def test_downloaded_content_must_match_the_current_tasks_file(self):
        for data in (b'unique-content', b'unique-content\n', b'unique-content\r\n'):
            delivered, checks = self.run_check([self.item()], {'download/result.txt': data})
            self.assertTrue(delivered)
            self.assertTrue(all(check['passed'] for check in checks))
        delivered, checks = self.run_check([self.item(task='other-task')], {'download/result.txt': b'unique-content'})
        self.assertFalse(delivered)
        self.assertFalse(any(check['passed'] for check in checks))

    def test_wrong_content_reports_a_delivery_with_failed_quality(self):
        delivered, checks = self.run_check([self.item()], {'download/result.txt': b'incorrect'})
        self.assertTrue(delivered)
        self.assertEqual([item['passed'] for item in checks], [True, False])
        result = {'sessions': [{'id': 'artifact', 'artifact_delivered': delivered}],
                  'tasks': [{'phase': 'artifact:deliver', 'state': 'completed'}],
                  'validation': {'checks': [{'passed': True}, *checks]}}
        task, _, delivery, quality = assess(load_case('J2-07'), result, 'failed')
        self.assertEqual(task['state'], 'completed')
        self.assertEqual(delivery['state'], 'delivered')
        self.assertEqual(len(quality['failed_checks']), 1)

    def test_absent_or_ambiguous_files_do_not_count_as_a_delivery(self):
        for items in ([], [self.item(), self.item(path='download/duplicate.txt')]):
            delivered, checks = self.run_check(items)
            self.assertFalse(delivered)
            self.assertFalse(any(check['passed'] for check in checks))

    def test_invalid_filename_and_escaping_download_path_are_rejected(self):
        with self.assertRaisesRegex(Failure, 'invalid_artifact_assertion'):
            self.run_check([], filename='../result.txt')
        with self.assertRaisesRegex(Failure, 'unsafe_artifact_path'):
            self.run_check([self.item(path='../outside')])

    def test_multiple_deliveries_cannot_reuse_the_wrong_file_or_task(self):
        self.run_check([self.item(), self.item(name='second.txt', path='download/second.txt')],
                       {'download/result.txt': b'unique-content', 'download/second.txt': b'second-content'})
        files = [{'filename': 'result.txt', 'content': 'unique-content'},
                 {'filename': 'second.txt', 'content': 'second-content'}]
        checks = []
        self.assertTrue(check_text_artifacts(self.execution, 'conversation', 'task', files, checks, 'artifact'))
        self.assertTrue(all(check['passed'] for check in checks))
        files[1]['content'] = 'wrong'
        checks = []
        check_text_artifacts(self.execution, 'conversation', 'task', files, checks, 'artifact')
        self.assertFalse(checks[-1]['passed'])

    def test_pdf_requires_parseable_download_and_actual_text(self):
        from pypdf import PdfWriter
        from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
        path = self.root / 'download/result.pdf'
        path.parent.mkdir()
        writer = PdfWriter()
        page = writer.add_blank_page(width=200, height=200)
        font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'),
                                 NameObject('/BaseFont'): NameObject('/Helvetica')})
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): font})})
        content = DecodedStreamObject(); content.set_data(b'BT /F1 12 Tf 10 100 Td (unique-content) Tj ET')
        page[NameObject('/Contents')] = writer._add_object(content)
        writer.write(path)
        self.inventory.write_text(json.dumps([self.item(name='result.pdf', path='download/result.pdf')]))
        for expected, passed in [('unique-content', True), ('missing-text', False)]:
            checks = []
            self.assertTrue(check_pdf_artifact(self.execution, 'conversation', 'task', 'result.pdf', expected, checks, 'pdf'))
            self.assertEqual(checks[-1]['passed'], passed)
        path.write_bytes(b'not a PDF')
        checks = []
        check_pdf_artifact(self.execution, 'conversation', 'task', 'result.pdf', 'unique-content', checks, 'pdf')
        self.assertFalse(checks[-1]['passed'])

    def workbook(self, cell, shared=None, target='worksheets/sheet1.xml'):
        path = self.root / 'test.xlsx'
        with zipfile.ZipFile(path, 'w') as bundle:
            bundle.writestr('xl/workbook.xml',
                '<workbook xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                '<sheets><sheet name="First" r:id="rId1"/></sheets></workbook>')
            bundle.writestr('xl/_rels/workbook.xml.rels',
                f'<Relationships><Relationship Id="rId1" Target="{target}"/></Relationships>')
            bundle.writestr('xl/worksheets/sheet1.xml', f'<worksheet><sheetData><row>{cell}</row></sheetData></worksheet>')
            if shared is not None:
                bundle.writestr('xl/sharedStrings.xml', shared)
        return path

    def test_real_workbook_encodings_resolve_first_sheet_a1(self):
        inline = self.workbook('<c r="A1" t="inlineStr"><is><t>unique-content</t></is></c>')
        self.assertEqual(xlsx_first_cell(inline), 'unique-content')
        shared = self.workbook('<c r="A1" t="s"><v>0</v></c>',
                               '<sst><si><r><t>unique-</t></r><r><t>content</t></r></si></sst>')
        self.assertEqual(xlsx_first_cell(shared), 'unique-content')

    def test_missing_or_escaping_worksheet_is_not_accepted(self):
        with self.assertRaises(ValueError):
            xlsx_first_cell(self.workbook('<c r="B1"><v>1</v></c>'))
        with self.assertRaises(ValueError):
            xlsx_first_cell(self.workbook('<c r="A1"><v>1</v></c>', target='../../other.xml'))

    def office_file(self, path, kind, *, unsafe=False, has_slide=True):
        main = 'word/document.xml' if kind == 'docx' else 'ppt/presentation.xml'
        content = ('wordprocessingml.document' if kind == 'docx' else 'presentationml.presentation')
        reltype = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
        with zipfile.ZipFile(path, 'w') as bundle:
            bundle.writestr('[Content_Types].xml',
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                f'<Override PartName="/{main}" ContentType="application/vnd.openxmlformats-officedocument.{content}.main+xml"/></Types>')
            bundle.writestr('_rels/.rels', '<Relationships><Relationship Id="main" '
                            f'Type="{reltype}officeDocument" Target="{main}"/></Relationships>')
            if kind == 'docx':
                body = ('<!DOCTYPE foo [<!ENTITY x "unsafe">]><foo/>' if unsafe else
                    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                    '<w:body><w:p><w:r><w:t>unique-</w:t></w:r><w:r><w:t>content</w:t></w:r></w:p></w:body></w:document>')
                bundle.writestr(main, body)
                bundle.writestr('word/comments.xml', '<comments>not the document body</comments>')
            else:
                slide = '<p:sldId r:id="rId1"/>' if has_slide else ''
                bundle.writestr(main, '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
                    f'xmlns:r="{reltype[:-1]}"><p:sldIdLst>{slide}</p:sldIdLst></p:presentation>')
                bundle.writestr('ppt/_rels/presentation.xml.rels', '<Relationships><Relationship Id="rId1" '
                                 f'Type="{reltype}slide" Target="slides/slide1.xml"/></Relationships>')
                bundle.writestr('ppt/slides/slide1.xml',
                    '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
                    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
                    '<a:p><a:r><a:t>unique-content</a:t></a:r></a:p></p:sld>')
                bundle.writestr('ppt/notesSlides/notesSlide1.xml', '<notes>not a slide</notes>')
        return path

    def test_word_and_slides_read_body_paragraphs_across_text_runs(self):
        document = self.office_file(self.root / 'test.docx', 'docx')
        self.assertEqual(office_paragraphs(document, 'docx'), ['unique-content'])
        slides = self.office_file(self.root / 'test.pptx', 'pptx')
        self.assertEqual(office_paragraphs(slides, 'pptx'), ['unique-content'])

    def test_office_artifact_checks_delivered_file_and_actual_body(self):
        path = self.root / 'download' / 'result.docx'
        path.parent.mkdir(parents=True)
        self.office_file(path, 'docx')
        self.inventory.write_text(json.dumps([self.item(name='result.docx', path='download/result.docx')]))
        checks = []
        delivered = check_ooxml_artifact(self.execution, 'conversation', 'task', 'result.docx',
                                          'unique-content', 'docx', checks, 'artifact')
        self.assertTrue(delivered)
        self.assertTrue(all(check['passed'] for check in checks))
        checks = []
        check_ooxml_artifact(self.execution, 'conversation', 'task', 'result.docx',
                             'different-content', 'docx', checks, 'artifact')
        self.assertEqual([check['passed'] for check in checks], [True, False])

    def test_invalid_or_unsafe_office_files_do_not_pass(self):
        document = self.office_file(self.root / 'unsafe.docx', 'docx', unsafe=True)
        with self.assertRaises(ValueError):
            office_paragraphs(document, 'docx')
        slides = self.office_file(self.root / 'missing.pptx', 'pptx', has_slide=False)
        with self.assertRaises(ValueError):
            office_paragraphs(slides, 'pptx')


if __name__ == '__main__':
    unittest.main()
