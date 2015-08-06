# -*- coding: utf-8 -*-
"""
lms/djangoapps/courseware/tests/test_submitting_problems.py
python ./manage.py lms test --verbosity=1 \
openedx/core/djangoapps/grading_policy/tests/test_courseware_submitting_problems.py --traceback --settings=test
"""
import unittest
from django.test.utils import override_settings
from courseware.tests.test_submitting_problems import TestSubmittingProblems
import json
import os
from textwrap import dedent

from django.conf import settings
from mock import patch
from nose.plugins.attrib import attr

from capa.tests.response_xml_factory import (
    CustomResponseXMLFactory, SchematicResponseXMLFactory, CodeResponseXMLFactory,
)
from courseware import grades
from courseware.models import StudentModule, StudentModuleHistory
from student.tests.factories import UserFactory
from student.models import anonymous_id_for_user
from xmodule.modulestore.tests.factories import CourseFactory, ItemFactory
from xmodule.partitions.partitions import Group, UserPartition
from openedx.core.djangoapps.credit.api import (
    set_credit_requirements, get_credit_requirement_status
)
from openedx.core.djangoapps.credit.models import CreditCourse, CreditProvider
from openedx.core.djangoapps.user_api.tests.factories import UserCourseTagFactory

FEATURES_WITH_CUSTOM_GRADING = settings.FEATURES.copy()
FEATURES_WITH_CUSTOM_GRADING['ENABLE_CUSTOM_GRADING'] = True


# pylint: disable=attribute-defined-outside-init
@unittest.skipIf(settings._SYSTEM == 'cms', 'Test for lms')  # pylint: disable=protected-access
@override_settings(FEATURES=FEATURES_WITH_CUSTOM_GRADING, ASSIGNMENT_GRADER='WeightedAssignmentFormatGrader')
class TestSubmittingProblemsVerticals(TestSubmittingProblems):
    """Overrides some methods needed for testing."""
    def score_for_hw(self, hw_url_name):
        """
        Returns list of scores for a given url.

        Returns list of scores for the given homework:
            [<points on problem_1>, <points on problem_2>, ..., <points on problem_n>]
        """

        # list of grade summaries for each section
        sections_list = []
        blocks = self.get_progress_summary()['blocks']
        for name, value in blocks.iteritems():  # pylint: disable=unused-variable
            if value['block_type'] == 'vertical':
                sections_list.append(value)
        # get the first section that matches the url (there should only be one)
        hw_section = next(section for section in sections_list if section.get('url_name') == hw_url_name)
        return [s.earned for s in hw_section['scores']]

    def add_graded_section_to_course(self, name, section_format='Homework', late=False, reset=False, showanswer=False, weight=1.0):  # pylint: disable=line-too-long, arguments-differ
        """
        Creates a graded homework section within a chapter and returns the section.
        """

        # if we don't already have a chapter create a new one
        if not hasattr(self, 'chapter'):
            self.chapter = ItemFactory.create(
                parent_location=self.course.location,
                category='chapter'
            )

        if late:
            section = ItemFactory.create(
                parent_location=self.chapter.location,
                display_name=name,
                category='vertical',
                metadata={'graded': True, 'format': section_format, 'due': '2013-05-20T23:30', 'weight': weight}
            )
        elif reset:
            section = ItemFactory.create(
                parent_location=self.chapter.location,
                display_name=name,
                category='vertical',
                rerandomize='always',
                metadata={'graded': True, 'format': section_format, 'weight': weight}
            )

        elif showanswer:
            section = ItemFactory.create(
                parent_location=self.chapter.location,
                display_name=name,
                category='vertical',
                showanswer='never',
                metadata={'graded': True, 'format': section_format, 'weight': weight}
            )

        else:
            section = ItemFactory.create(
                parent_location=self.chapter.location,
                display_name=name,
                category='vertical',
                metadata={'graded': True, 'format': section_format, 'weight': weight}
            )

        # now that we've added the problem and section to the course
        # we fetch the course from the database so the object we are
        # dealing with has these additions
        self.refresh_course()
        return section


# pylint: disable=attribute-defined-outside-init
@attr('shard_1')
class TestCourseGrader(TestSubmittingProblemsVerticals):
    """
    Suite of tests for the course grader.
    """

    def basic_setup(self, late=False, reset=False, showanswer=False):
        """
        Set up a simple course for testing basic grading functionality.
        """

        grading_policy = {
            "GRADER": [{
                "type": "Homework",
                "min_count": 1,
                "drop_count": 0,
                "short_label": "HW",
                "weight": 1.0
            }],
            "GRADE_CUTOFFS": {
                'A': .9,
                'B': .33
            }
        }
        self.add_grading_policy(grading_policy)

        # set up a simple course with four problems
        self.homework = self.add_graded_section_to_course('homework', late=late, reset=reset, showanswer=showanswer)
        self.add_dropdown_to_section(self.homework.location, 'p1', 1)
        self.add_dropdown_to_section(self.homework.location, 'p2', 1)
        self.add_dropdown_to_section(self.homework.location, 'p3', 1)
        self.refresh_course()

    def weighted_setup(self):
        """
        Set up a simple course for testing weighted grading functionality.
        """

        grading_policy = {
            "GRADER": [
                {
                    "type": "Homework",
                    "min_count": 1,
                    "drop_count": 0,
                    "short_label": "HW",
                    "weight": 0.25
                }, {
                    "type": "Final",
                    "name": "Final Section",
                    "short_label": "Final",
                    "weight": 0.75
                }
            ]
        }
        self.add_grading_policy(grading_policy)

        # set up a structure of 1 homework and 1 final
        self.homework = self.add_graded_section_to_course('homework')
        self.problem = self.add_dropdown_to_section(self.homework.location, 'H1P1')
        self.final = self.add_graded_section_to_course('Final Section', 'Final')
        self.final_question = self.add_dropdown_to_section(self.final.location, 'FinalQuestion')

    def dropping_setup(self):
        """
        Set up a simple course for testing the dropping grading functionality.
        """

        grading_policy = {
            "GRADER": [
                {
                    "type": "Homework",
                    "min_count": 3,
                    "drop_count": 1,
                    "short_label": "HW",
                    "weight": 1
                }
            ]
        }
        self.add_grading_policy(grading_policy)

        # Set up a course structure that just consists of 3 homeworks.
        # Since the grading policy drops 1 entire homework, each problem is worth 25%

        # names for the problem in the homeworks
        self.hw1_names = ['h1p1', 'h1p2']
        self.hw2_names = ['h2p1', 'h2p2']
        self.hw3_names = ['h3p1', 'h3p2']

        self.homework1 = self.add_graded_section_to_course('homework1', weight=0.5)
        self.add_dropdown_to_section(self.homework1.location, self.hw1_names[0], 1)
        self.add_dropdown_to_section(self.homework1.location, self.hw1_names[1], 1)
        self.homework2 = self.add_graded_section_to_course('homework2', weight=0.3)
        self.add_dropdown_to_section(self.homework2.location, self.hw2_names[0], 1)
        self.add_dropdown_to_section(self.homework2.location, self.hw2_names[1], 1)
        self.homework3 = self.add_graded_section_to_course('homework3', weight=0.2)
        self.add_dropdown_to_section(self.homework3.location, self.hw3_names[0], 1)
        self.add_dropdown_to_section(self.homework3.location, self.hw3_names[1], 1)

    def test_submission_late(self):
        """Test problem for due date in the past"""
        self.basic_setup(late=True)
        resp = self.submit_question_answer('p1', {'2_1': 'Correct'})
        self.assertEqual(resp.status_code, 200)
        err_msg = (
            "The state of this problem has changed since you loaded this page. "
            "Please refresh your page."
        )
        self.assertEqual(json.loads(resp.content).get("success"), err_msg)

    def test_submission_reset(self):
        """Test problem ProcessingErrors due to resets"""
        self.basic_setup(reset=True)
        resp = self.submit_question_answer('p1', {'2_1': 'Correct'})
        #  submit a second time to draw NotFoundError
        resp = self.submit_question_answer('p1', {'2_1': 'Correct'})
        self.assertEqual(resp.status_code, 200)
        err_msg = (
            "The state of this problem has changed since you loaded this page. "
            "Please refresh your page."
        )
        self.assertEqual(json.loads(resp.content).get("success"), err_msg)

    def test_submission_show_answer(self):
        """Test problem for ProcessingErrors due to showing answer"""
        self.basic_setup(showanswer=True)
        resp = self.show_question_answer('p1')
        self.assertEqual(resp.status_code, 200)
        err_msg = (
            "The state of this problem has changed since you loaded this page. "
            "Please refresh your page."
        )
        self.assertEqual(json.loads(resp.content).get("success"), err_msg)

    def test_show_answer_doesnt_write_to_csm(self):
        self.basic_setup()
        self.submit_question_answer('p1', {'2_1': u'Correct'})

        # Now fetch the state entry for that problem.
        student_module = StudentModule.objects.get(
            course_id=self.course.id,
            student=self.student_user
        )
        # count how many state history entries there are
        baseline = StudentModuleHistory.objects.filter(
            student_module=student_module
        )
        baseline_count = baseline.count()
        self.assertEqual(baseline_count, 3)

        # now click "show answer"
        self.show_question_answer('p1')

        # check that we don't have more state history entries
        csmh = StudentModuleHistory.objects.filter(
            student_module=student_module
        )
        current_count = csmh.count()
        self.assertEqual(current_count, 3)

    def test_grade_with_max_score_cache(self):
        """
        Tests that the max score cache is populated after a grading run
        and that the results of grading runs before and after the cache
        warms are the same.
        """
        self.basic_setup()
        self.submit_question_answer('p1', {'2_1': 'Correct'})
        self.look_at_question('p2')
        self.assertTrue(
            StudentModule.objects.filter(
                module_state_key=self.problem_location('p2')
            ).exists()
        )
        location_to_cache = unicode(self.problem_location('p2'))
        max_scores_cache = grades.MaxScoresCache.create_for_course(self.course)

        # problem isn't in the cache
        max_scores_cache.fetch_from_remote([location_to_cache])
        self.assertIsNone(max_scores_cache.get(location_to_cache))
        self.check_grade_percent(0.33)

        # problem is in the cache
        max_scores_cache.fetch_from_remote([location_to_cache])
        self.assertIsNotNone(max_scores_cache.get(location_to_cache))
        self.check_grade_percent(0.33)

    def test_none_grade(self):
        """
        Check grade is 0 to begin with.
        """
        self.basic_setup()
        self.check_grade_percent(0)
        self.assertEqual(self.get_grade_summary()['grade'], None)

    def test_b_grade_exact(self):
        """
        Check that at exactly the cutoff, the grade is B.
        """
        self.basic_setup()
        self.submit_question_answer('p1', {'2_1': 'Correct'})
        self.check_grade_percent(0.33)
        self.assertEqual(self.get_grade_summary()['grade'], 'B')

    @patch.dict("django.conf.settings.FEATURES", {"ENABLE_MAX_SCORE_CACHE": False})
    def test_grade_no_max_score_cache(self):
        """
        Tests grading when the max score cache is disabled
        """
        self.test_b_grade_exact()

    def test_b_grade_above(self):
        """
        Check grade between cutoffs.
        """
        self.basic_setup()
        self.submit_question_answer('p1', {'2_1': 'Correct'})
        self.submit_question_answer('p2', {'2_1': 'Correct'})
        self.check_grade_percent(0.67)
        self.assertEqual(self.get_grade_summary()['grade'], 'B')

    def test_a_grade(self):
        """
        Check that 100 percent completion gets an A
        """
        self.basic_setup()
        self.submit_question_answer('p1', {'2_1': 'Correct'})
        self.submit_question_answer('p2', {'2_1': 'Correct'})
        self.submit_question_answer('p3', {'2_1': 'Correct'})
        self.check_grade_percent(1.0)
        self.assertEqual(self.get_grade_summary()['grade'], 'A')

    def test_wrong_answers(self):
        """
        Check that answering incorrectly is graded properly.
        """
        self.basic_setup()
        self.submit_question_answer('p1', {'2_1': 'Correct'})
        self.submit_question_answer('p2', {'2_1': 'Correct'})
        self.submit_question_answer('p3', {'2_1': 'Incorrect'})
        self.check_grade_percent(0.67)
        self.assertEqual(self.get_grade_summary()['grade'], 'B')

    def test_submissions_api_overrides_scores(self):
        """
        Check that answering incorrectly is graded properly.
        """
        self.basic_setup()
        self.submit_question_answer('p1', {'2_1': 'Correct'})
        self.submit_question_answer('p2', {'2_1': 'Correct'})
        self.submit_question_answer('p3', {'2_1': 'Incorrect'})
        self.check_grade_percent(0.67)
        self.assertEqual(self.get_grade_summary()['grade'], 'B')

        # But now we mock out a get_scores call, and watch as it overrides the
        # score read from StudentModule and our student gets an A instead.
        with patch('submissions.api.get_scores') as mock_get_scores:
            mock_get_scores.return_value = {
                self.problem_location('p3').to_deprecated_string(): (1, 1)
            }
            self.check_grade_percent(1.0)
            self.assertEqual(self.get_grade_summary()['grade'], 'A')

    def test_submissions_api_anonymous_student_id(self):
        """
        Check that the submissions API is sent an anonymous student ID.
        """
        self.basic_setup()
        self.submit_question_answer('p1', {'2_1': 'Correct'})
        self.submit_question_answer('p2', {'2_1': 'Correct'})
        self.submit_question_answer('p3', {'2_1': 'Incorrect'})

        with patch('submissions.api.get_scores') as mock_get_scores:
            mock_get_scores.return_value = {
                self.problem_location('p3').to_deprecated_string(): (1, 1)
            }
            self.get_grade_summary()

            # Verify that the submissions API was sent an anonymized student ID
            mock_get_scores.assert_called_with(
                self.course.id.to_deprecated_string(),
                anonymous_id_for_user(self.student_user, self.course.id)
            )

    def test_weighted_homework(self):
        """
        Test that the homework section has proper weight.
        """
        self.weighted_setup()

        # Get both parts correct
        self.submit_question_answer('H1P1', {'2_1': 'Correct', '2_2': 'Correct'})
        self.check_grade_percent(0.25)
        self.assertEqual(self.earned_hw_scores(), [2.0])  # Order matters
        self.assertEqual(self.score_for_hw('homework'), [2.0])

    def test_weighted_exam(self):
        """
        Test that the exam section has the proper weight.
        """
        self.weighted_setup()
        self.submit_question_answer('FinalQuestion', {'2_1': 'Correct', '2_2': 'Correct'})
        self.check_grade_percent(0.75)

    def test_weighted_total(self):
        """
        Test that the weighted total adds to 100.
        """
        self.weighted_setup()
        self.submit_question_answer('H1P1', {'2_1': 'Correct', '2_2': 'Correct'})
        self.submit_question_answer('FinalQuestion', {'2_1': 'Correct', '2_2': 'Correct'})
        self.check_grade_percent(1.0)

    def dropping_homework_stage1(self):
        """
        Get half the first homework correct and all of the second
        """
        self.submit_question_answer(self.hw1_names[0], {'2_1': 'Correct'})
        self.submit_question_answer(self.hw1_names[1], {'2_1': 'Incorrect'})
        for name in self.hw2_names:
            self.submit_question_answer(name, {'2_1': 'Correct'})

    def test_dropping_grades_normally(self):
        """
        Test that the dropping policy does not change things before it should.
        """
        self.dropping_setup()
        self.dropping_homework_stage1()

        self.assertEqual(self.score_for_hw('homework1'), [1.0, 0.0])
        self.assertEqual(self.score_for_hw('homework2'), [1.0, 1.0])
        self.assertEqual(self.earned_hw_scores(), [1.0, 2.0, 0])  # Order matters
        self.check_grade_percent(0.69)  # (0.5 * 0.5 + 0.3 * 1.0) / (1.0 + 0.2)

    def test_dropping_nochange(self):
        """
        Tests that grade does not change when making the global homework grade minimum not unique.
        """
        self.dropping_setup()
        self.dropping_homework_stage1()
        self.submit_question_answer(self.hw3_names[0], {'2_1': 'Correct'})

        self.assertEqual(self.score_for_hw('homework1'), [1.0, 0.0])
        self.assertEqual(self.score_for_hw('homework2'), [1.0, 1.0])
        self.assertEqual(self.score_for_hw('homework3'), [1.0, 0.0])
        self.assertEqual(self.earned_hw_scores(), [1.0, 2.0, 1.0])  # Order matters
        self.check_grade_percent(0.69)  # (0.5 * 0.5 + 0.3 * 1.0) / (1.0 + 0.2)

    def test_dropping_all_correct(self):
        """
        Test that the lowest is dropped for a perfect score.
        """
        self.dropping_setup()

        self.dropping_homework_stage1()
        for name in self.hw3_names:
            self.submit_question_answer(name, {'2_1': 'Correct'})

        #  HW3 is a lowest dropped weighted score 0.2 * 1.0 (HW3) < 0.5 * 0.5 (HW1) < 0.3 * 1.0 (HW2)
        self.check_grade_percent(0.69)
        self.assertEqual(self.earned_hw_scores(), [1.0, 2.0, 2.0])  # Order matters
        self.assertEqual(self.score_for_hw('homework3'), [1.0, 1.0])

    def test_min_grade_credit_requirements_status(self):
        """
        Test for credit course. If user passes minimum grade requirement then
        status will be updated as satisfied in requirement status table.
        """
        self.basic_setup()
        self.submit_question_answer('p1', {'2_1': 'Correct'})
        self.submit_question_answer('p2', {'2_1': 'Correct'})

        # Enable the course for credit
        credit_course = CreditCourse.objects.create(  # pylint: disable=unused-variable
            course_key=self.course.id,
            enabled=True,
        )

        # Configure a credit provider for the course
        CreditProvider.objects.create(
            provider_id="ASU",
            enable_integration=True,
            provider_url="https://credit.example.com/request",
        )

        requirements = [{
            "namespace": "grade",
            "name": "grade",
            "display_name": "Grade",
            "criteria": {"min_grade": 0.52},
        }]
        # Add a single credit requirement (final grade)
        set_credit_requirements(self.course.id, requirements)

        self.get_grade_summary()
        req_status = get_credit_requirement_status(self.course.id, self.student_user.username, 'grade', 'grade')
        self.assertEqual(req_status[0]["status"], 'satisfied')


# pylint: disable=attribute-defined-outside-init
@attr('shard_1')
class ProblemWithUploadedFilesTest(TestSubmittingProblemsVerticals):
    """Tests of problems with uploaded files."""

    def setUp(self):
        super(ProblemWithUploadedFilesTest, self).setUp()
        self.section = self.add_graded_section_to_course('section')

    def problem_setup(self, name, files):
        """
        Create a CodeResponse problem with files to upload.
        """

        xmldata = CodeResponseXMLFactory().build_xml(
            allowed_files=files, required_files=files,
        )
        ItemFactory.create(
            parent_location=self.section.location,
            category='problem',
            display_name=name,
            data=xmldata
        )

        # re-fetch the course from the database so the object is up to date
        self.refresh_course()

    def test_three_files(self):
        # Open the test files, and arrange to close them later.
        filenames = "prog1.py prog2.py prog3.py"
        fileobjs = [
            open(os.path.join(settings.COMMON_TEST_DATA_ROOT, "capa", filename))
            for filename in filenames.split()
        ]
        for fileobj in fileobjs:
            self.addCleanup(fileobj.close)

        self.problem_setup("the_problem", filenames)
        with patch('courseware.module_render.XQUEUE_INTERFACE.session') as mock_session:
            resp = self.submit_question_answer("the_problem", {'2_1': fileobjs})

        self.assertEqual(resp.status_code, 200)
        json_resp = json.loads(resp.content)
        self.assertEqual(json_resp['success'], "incorrect")

        # See how post got called.
        name, args, kwargs = mock_session.mock_calls[0]
        self.assertEqual(name, "post")
        self.assertEqual(len(args), 1)
        self.assertTrue(args[0].endswith("/submit/"))
        self.assertItemsEqual(kwargs.keys(), ["files", "data"])
        self.assertItemsEqual(kwargs['files'].keys(), filenames.split())


# pylint: disable=attribute-defined-outside-init
@attr('shard_1')
class TestPythonGradedResponse(TestSubmittingProblemsVerticals):
    """
    Check that we can submit a schematic and custom response, and it answers properly.
    """

    SCHEMATIC_SCRIPT = dedent("""
        # for a schematic response, submission[i] is the json representation
        # of the diagram and analysis results for the i-th schematic tag

        def get_tran(json,signal):
          for element in json:
            if element[0] == 'transient':
              return element[1].get(signal,[])
          return []

        def get_value(at,output):
          for (t,v) in output:
            if at == t: return v
          return None

        output = get_tran(submission[0],'Z')
        okay = True

        # output should be 1, 1, 1, 1, 1, 0, 0, 0
        if get_value(0.0000004, output) < 2.7: okay = False;
        if get_value(0.0000009, output) < 2.7: okay = False;
        if get_value(0.0000014, output) < 2.7: okay = False;
        if get_value(0.0000019, output) < 2.7: okay = False;
        if get_value(0.0000024, output) < 2.7: okay = False;
        if get_value(0.0000029, output) > 0.25: okay = False;
        if get_value(0.0000034, output) > 0.25: okay = False;
        if get_value(0.0000039, output) > 0.25: okay = False;

        correct = ['correct' if okay else 'incorrect']""").strip()

    SCHEMATIC_CORRECT = json.dumps(
        [['transient', {'Z': [
            [0.0000004, 2.8],
            [0.0000009, 2.8],
            [0.0000014, 2.8],
            [0.0000019, 2.8],
            [0.0000024, 2.8],
            [0.0000029, 0.2],
            [0.0000034, 0.2],
            [0.0000039, 0.2]
        ]}]]
    )

    SCHEMATIC_INCORRECT = json.dumps(
        [['transient', {'Z': [
            [0.0000004, 2.8],
            [0.0000009, 0.0],  # wrong.
            [0.0000014, 2.8],
            [0.0000019, 2.8],
            [0.0000024, 2.8],
            [0.0000029, 0.2],
            [0.0000034, 0.2],
            [0.0000039, 0.2]
        ]}]]
    )

    CUSTOM_RESPONSE_SCRIPT = dedent("""
        def test_csv(expect, ans):
            # Take out all spaces in expected answer
            expect = [i.strip(' ') for i in str(expect).split(',')]
            # Take out all spaces in student solution
            ans = [i.strip(' ') for i in str(ans).split(',')]

            def strip_q(x):
                # Strip quotes around strings if students have entered them
                stripped_ans = []
                for item in x:
                    if item[0] == "'" and item[-1]=="'":
                        item = item.strip("'")
                    elif item[0] == '"' and item[-1] == '"':
                        item = item.strip('"')
                    stripped_ans.append(item)
                return stripped_ans

            return strip_q(expect) == strip_q(ans)""").strip()

    CUSTOM_RESPONSE_CORRECT = "0, 1, 2, 3, 4, 5, 'Outside of loop', 6"
    CUSTOM_RESPONSE_INCORRECT = "Reading my code I see.  I hope you like it :)"

    COMPUTED_ANSWER_SCRIPT = dedent("""
        if submission[0] == "a shout in the street":
            correct = ['correct']
        else:
            correct = ['incorrect']""").strip()

    COMPUTED_ANSWER_CORRECT = "a shout in the street"
    COMPUTED_ANSWER_INCORRECT = "because we never let them in"

    def setUp(self):
        super(TestPythonGradedResponse, self).setUp()
        self.section = self.add_graded_section_to_course('section')
        self.correct_responses = {}
        self.incorrect_responses = {}

    def schematic_setup(self, name):
        """
        set up an example Circuit_Schematic_Builder problem
        """

        script = self.SCHEMATIC_SCRIPT

        xmldata = SchematicResponseXMLFactory().build_xml(answer=script)
        ItemFactory.create(
            parent_location=self.section.location,
            category='problem',
            boilerplate='circuitschematic.yaml',
            display_name=name,
            data=xmldata
        )

        # define the correct and incorrect responses to this problem
        self.correct_responses[name] = self.SCHEMATIC_CORRECT
        self.incorrect_responses[name] = self.SCHEMATIC_INCORRECT

        # re-fetch the course from the database so the object is up to date
        self.refresh_course()

    def custom_response_setup(self, name):
        """
        set up an example custom response problem using a check function
        """

        test_csv = self.CUSTOM_RESPONSE_SCRIPT
        expect = self.CUSTOM_RESPONSE_CORRECT
        cfn_problem_xml = CustomResponseXMLFactory().build_xml(script=test_csv, cfn='test_csv', expect=expect)

        ItemFactory.create(
            parent_location=self.section.location,
            category='problem',
            boilerplate='customgrader.yaml',
            data=cfn_problem_xml,
            display_name=name
        )

        # define the correct and incorrect responses to this problem
        self.correct_responses[name] = expect
        self.incorrect_responses[name] = self.CUSTOM_RESPONSE_INCORRECT

        # re-fetch the course from the database so the object is up to date
        self.refresh_course()

    def computed_answer_setup(self, name):
        """
        set up an example problem using an answer script'''
        """

        script = self.COMPUTED_ANSWER_SCRIPT

        computed_xml = CustomResponseXMLFactory().build_xml(answer=script)

        ItemFactory.create(
            parent_location=self.section.location,
            category='problem',
            boilerplate='customgrader.yaml',
            data=computed_xml,
            display_name=name
        )

        # define the correct and incorrect responses to this problem
        self.correct_responses[name] = self.COMPUTED_ANSWER_CORRECT
        self.incorrect_responses[name] = self.COMPUTED_ANSWER_INCORRECT

        # re-fetch the course from the database so the object is up to date
        self.refresh_course()

    def _check_correct(self, name):
        """
        check that problem named "name" gets evaluated correctly correctly
        """
        resp = self.submit_question_answer(name, {'2_1': self.correct_responses[name]})

        respdata = json.loads(resp.content)
        self.assertEqual(respdata['success'], 'correct')

    def _check_incorrect(self, name):
        """
        check that problem named "name" gets evaluated incorrectly correctly
        """
        resp = self.submit_question_answer(name, {'2_1': self.incorrect_responses[name]})

        respdata = json.loads(resp.content)
        self.assertEqual(respdata['success'], 'incorrect')

    def _check_ireset(self, name):
        """
        Check that the problem can be reset
        """
        # first, get the question wrong
        resp = self.submit_question_answer(name, {'2_1': self.incorrect_responses[name]})
        # reset the question
        self.reset_question_answer(name)
        # then get it right
        resp = self.submit_question_answer(name, {'2_1': self.correct_responses[name]})

        respdata = json.loads(resp.content)
        self.assertEqual(respdata['success'], 'correct')

    def test_schematic_correct(self):
        name = "schematic_problem"
        self.schematic_setup(name)
        self._check_correct(name)

    def test_schematic_incorrect(self):
        name = "schematic_problem"
        self.schematic_setup(name)
        self._check_incorrect(name)

    def test_schematic_reset(self):
        name = "schematic_problem"
        self.schematic_setup(name)
        self._check_ireset(name)

    def test_check_function_correct(self):
        name = 'cfn_problem'
        self.custom_response_setup(name)
        self._check_correct(name)

    def test_check_function_incorrect(self):
        name = 'cfn_problem'
        self.custom_response_setup(name)
        self._check_incorrect(name)

    def test_check_function_reset(self):
        name = 'cfn_problem'
        self.custom_response_setup(name)
        self._check_ireset(name)

    def test_computed_correct(self):
        name = 'computed_answer'
        self.computed_answer_setup(name)
        self._check_correct(name)

    def test_computed_incorrect(self):
        name = 'computed_answer'
        self.computed_answer_setup(name)
        self._check_incorrect(name)

    def test_computed_reset(self):
        name = 'computed_answer'
        self.computed_answer_setup(name)
        self._check_ireset(name)


# pylint: disable=attribute-defined-outside-init
@attr('shard_1')
class TestAnswerDistributions(TestSubmittingProblemsVerticals):
    """Check that we can pull answer distributions for problems."""

    def setUp(self):
        """Set up a simple course with four problems."""
        super(TestAnswerDistributions, self).setUp()

        self.homework = self.add_graded_section_to_course('homework')
        self.p1_html_id = self.add_dropdown_to_section(self.homework.location, 'p1', 1).location.html_id()
        self.p2_html_id = self.add_dropdown_to_section(self.homework.location, 'p2', 1).location.html_id()
        self.p3_html_id = self.add_dropdown_to_section(self.homework.location, 'p3', 1).location.html_id()
        self.refresh_course()

    def test_empty(self):
        # Just make sure we can process this without errors.
        empty_distribution = grades.answer_distributions(self.course.id)
        self.assertFalse(empty_distribution)  # should be empty

    def test_one_student(self):
        # Basic test to make sure we have simple behavior right for a student

        # Throw in a non-ASCII answer
        self.submit_question_answer('p1', {'2_1': u'ⓤⓝⓘⓒⓞⓓⓔ'})
        self.submit_question_answer('p2', {'2_1': 'Correct'})

        distributions = grades.answer_distributions(self.course.id)
        self.assertEqual(
            distributions,
            {
                ('p1', 'p1', '{}_2_1'.format(self.p1_html_id)): {
                    u'ⓤⓝⓘⓒⓞⓓⓔ': 1
                },
                ('p2', 'p2', '{}_2_1'.format(self.p2_html_id)): {
                    'Correct': 1
                }
            }
        )

    def test_multiple_students(self):
        # Our test class is based around making requests for a particular user,
        # so we're going to cheat by creating another user and copying and
        # modifying StudentModule entries to make them from other users. It's
        # a little hacky, but it seemed the simpler way to do this.
        self.submit_question_answer('p1', {'2_1': u'Correct'})
        self.submit_question_answer('p2', {'2_1': u'Incorrect'})
        self.submit_question_answer('p3', {'2_1': u'Correct'})

        # Make the above submissions owned by user2
        user2 = UserFactory.create()
        problems = StudentModule.objects.filter(
            course_id=self.course.id,
            student=self.student_user
        )
        for problem in problems:
            problem.student_id = user2.id
            problem.save()

        # Now make more submissions by our original user
        self.submit_question_answer('p1', {'2_1': u'Correct'})
        self.submit_question_answer('p2', {'2_1': u'Correct'})

        self.assertEqual(
            grades.answer_distributions(self.course.id),
            {
                ('p1', 'p1', '{}_2_1'.format(self.p1_html_id)): {
                    'Correct': 2
                },
                ('p2', 'p2', '{}_2_1'.format(self.p2_html_id)): {
                    'Correct': 1,
                    'Incorrect': 1
                },
                ('p3', 'p3', '{}_2_1'.format(self.p3_html_id)): {
                    'Correct': 1
                }
            }
        )

    def test_other_data_types(self):
        # We'll submit one problem, and then muck with the student_answers
        # dict inside its state to try different data types (str, int, float,
        # none)
        self.submit_question_answer('p1', {'2_1': u'Correct'})

        # Now fetch the state entry for that problem.
        student_module = StudentModule.objects.get(
            course_id=self.course.id,
            student=self.student_user
        )
        for val in ('Correct', True, False, 0, 0.0, 1, 1.0, None):
            state = json.loads(student_module.state)
            state["student_answers"]['{}_2_1'.format(self.p1_html_id)] = val
            student_module.state = json.dumps(state)
            student_module.save()

            self.assertEqual(
                grades.answer_distributions(self.course.id),
                {
                    ('p1', 'p1', '{}_2_1'.format(self.p1_html_id)): {
                        str(val): 1
                    },
                }
            )

    def test_missing_content(self):
        # If there's a StudentModule entry for content that no longer exists,
        # we just quietly ignore it (because we can't display a meaningful url
        # or name for it).
        self.submit_question_answer('p1', {'2_1': 'Incorrect'})

        # Now fetch the state entry for that problem and alter it so it points
        # to a non-existent problem.
        student_module = StudentModule.objects.get(
            course_id=self.course.id,
            student=self.student_user
        )
        student_module.module_state_key = student_module.module_state_key.replace(
            name=student_module.module_state_key.name + "_fake"
        )
        student_module.save()

        # It should be empty (ignored)
        empty_distribution = grades.answer_distributions(self.course.id)
        self.assertFalse(empty_distribution)  # should be empty

    def test_broken_state(self):
        # Missing or broken state for a problem should be skipped without
        # causing the whole answer_distribution call to explode.

        # Submit p1
        self.submit_question_answer('p1', {'2_1': u'Correct'})

        # Now fetch the StudentModule entry for p1 so we can corrupt its state
        prb1 = StudentModule.objects.get(
            course_id=self.course.id,
            student=self.student_user
        )

        # Submit p2
        self.submit_question_answer('p2', {'2_1': u'Incorrect'})

        for new_p1_state in ('{"student_answers": {}}', "invalid json!", None):
            prb1.state = new_p1_state
            prb1.save()

            # p1 won't show up, but p2 should still work
            self.assertEqual(
                grades.answer_distributions(self.course.id),
                {
                    ('p2', 'p2', '{}_2_1'.format(self.p2_html_id)): {
                        'Incorrect': 1
                    },
                }
            )


# pylint: disable=attribute-defined-outside-init
@attr('shard_1')
class TestConditionalContent(TestSubmittingProblemsVerticals):
    """
    Check that conditional content works correctly with grading.
    """

    def setUp(self):
        """
        Set up a simple course with a grading policy, a UserPartition, and 2 sections, both graded as "homework".
        One section is pre-populated with a problem (with 2 inputs), visible to all students.
        The second section is empty. Test cases should add conditional content to it.
        """
        super(TestConditionalContent, self).setUp()

        self.user_partition_group_0 = 0
        self.user_partition_group_1 = 1
        self.partition = UserPartition(
            0,
            'first_partition',
            'First Partition',
            [
                Group(self.user_partition_group_0, 'alpha'),
                Group(self.user_partition_group_1, 'beta')
            ]
        )

        self.course = CourseFactory.create(
            display_name=self.COURSE_NAME,
            number=self.COURSE_SLUG,
            user_partitions=[self.partition]
        )

        grading_policy = {
            "GRADER": [{
                "type": "Homework",
                "min_count": 2,
                "drop_count": 0,
                "short_label": "HW",
                "weight": 1.0
            }]
        }
        self.add_grading_policy(grading_policy)

        self.homework_all = self.add_graded_section_to_course('homework1', weight=0.8)
        self.p1_all_html_id = self.add_dropdown_to_section(self.homework_all.location, 'H1P1', 2).location.html_id()

        self.homework_conditional = self.add_graded_section_to_course('homework2', weight=0.2)

    def split_setup(self, user_partition_group):
        """
        Setup for tests using split_test module. Creates a split_test instance as a child of self.homework_conditional
        with 2 verticals in it, and assigns self.student_user to the specified user_partition_group.

        The verticals are returned.
        """
        vertical_0_url = self.course.id.make_usage_key("vertical", "split_test_vertical_0")
        vertical_1_url = self.course.id.make_usage_key("vertical", "split_test_vertical_1")

        group_id_to_child = {}
        for index, url in enumerate([vertical_0_url, vertical_1_url]):
            group_id_to_child[str(index)] = url

        split_test = ItemFactory.create(
            parent_location=self.homework_conditional.location,
            category="split_test",
            display_name="Split test",
            user_partition_id='0',
            group_id_to_child=group_id_to_child,
        )

        vertical_0 = ItemFactory.create(
            parent_location=split_test.location,
            category="vertical",
            display_name="Condition 0 vertical",
            location=vertical_0_url,
        )

        vertical_1 = ItemFactory.create(
            parent_location=split_test.location,
            category="vertical",
            display_name="Condition 1 vertical",
            location=vertical_1_url,
        )

        # Now add the student to the specified group.
        UserCourseTagFactory(
            user=self.student_user,
            course_id=self.course.id,
            key='xblock.partition_service.partition_{0}'.format(self.partition.id),  # pylint: disable=no-member
            value=str(user_partition_group)
        )

        return vertical_0, vertical_1

    def split_different_problems_setup(self, user_partition_group):
        """
        Setup for the case where the split test instance contains problems for each group
        (so both groups do have graded content, though it is different).

        Group 0 has 2 problems, worth 1 and 3 points respectively.
        Group 1 has 1 problem, worth 1 point.

        This method also assigns self.student_user to the specified user_partition_group and
        then submits answers for the problems in section 1, which are visible to all students.
        The submitted answers give the student 1 point out of a possible 2 points in the section.
        """
        vertical_0, vertical_1 = self.split_setup(user_partition_group)

        # Group 0 will have 2 problems in the section, worth a total of 4 points.
        self.add_dropdown_to_section(vertical_0.location, 'H2P1_GROUP0', 1).location.html_id()
        self.add_dropdown_to_section(vertical_0.location, 'H2P2_GROUP0', 3).location.html_id()

        # Group 1 will have 1 problem in the section, worth a total of 1 point.
        self.add_dropdown_to_section(vertical_1.location, 'H2P1_GROUP1', 1).location.html_id()

        # Submit answers for problem in Section 1, which is visible to all students.
        self.submit_question_answer('H1P1', {'2_1': 'Correct', '2_2': 'Incorrect'})

    def test_split_different_problems_group_0(self):
        """
        Tests that users who see different problems in a split_test module instance are graded correctly.
        This is the test case for a user in user partition group 0.
        """
        self.split_different_problems_setup(self.user_partition_group_0)

        self.submit_question_answer('H2P1_GROUP0', {'2_1': 'Correct'})
        self.submit_question_answer('H2P2_GROUP0', {'2_1': 'Correct', '2_2': 'Incorrect', '2_3': 'Correct'})

        self.assertEqual(self.score_for_hw('homework1'), [1.0])
        self.assertEqual(self.score_for_hw('homework2'), [1.0, 2.0])
        self.assertEqual(self.earned_hw_scores(), [1.0, 3.0])

        # Grade percent is .55. Here is the calculation
        homework_1_score = (1.0 / 2) * 0.8
        homework_2_score = ((1.0 + 2.0) / 4) * 0.2
        self.check_grade_percent(round((homework_1_score + homework_2_score), 2))

    def test_split_different_problems_group_1(self):
        """
        Tests that users who see different problems in a split_test module instance are graded correctly.
        This is the test case for a user in user partition group 1.
        """
        self.split_different_problems_setup(self.user_partition_group_1)

        self.submit_question_answer('H2P1_GROUP1', {'2_1': 'Correct'})

        self.assertEqual(self.score_for_hw('homework1'), [1.0])
        self.assertEqual(self.score_for_hw('homework2'), [1.0])
        self.assertEqual(self.earned_hw_scores(), [1.0, 1.0])

        # Grade percent is .6. Here is the calculation
        homework_1_score = (1.0 / 2) * 0.8
        homework_2_score = (1.0 / 1) * 0.2
        self.check_grade_percent(round((homework_1_score + homework_2_score), 2))

    def split_one_group_no_problems_setup(self, user_partition_group):
        """
        Setup for the case where the split test instance contains problems on for one group.

        Group 0 has no problems.
        Group 1 has 1 problem, worth 1 point.

        This method also assigns self.student_user to the specified user_partition_group and
        then submits answers for the problems in section 1, which are visible to all students.
        The submitted answers give the student 2 points out of a possible 2 points in the section.
        """
        [_, vertical_1] = self.split_setup(user_partition_group)

        # Group 1 will have 1 problem in the section, worth a total of 1 point.
        self.add_dropdown_to_section(vertical_1.location, 'H2P1_GROUP1', 1).location.html_id()

        self.submit_question_answer('H1P1', {'2_1': 'Correct'})

    def test_split_one_group_no_problems_group_0(self):
        """
        Tests what happens when a given group has no problems in it (students receive 0 for that section).
        """
        self.split_one_group_no_problems_setup(self.user_partition_group_0)

        self.assertEqual(self.score_for_hw('homework1'), [1.0])
        self.assertEqual(self.score_for_hw('homework2'), [])
        self.assertEqual(self.earned_hw_scores(), [1.0, 0.0])

        # Grade percent is .4. Here is the calculation.
        homework_1_score = (1.0 / 2) * 0.8
        homework_2_score = 0.0
        self.check_grade_percent(round((homework_1_score + homework_2_score), 2))

    def test_split_one_group_no_problems_group_1(self):
        """
        Verifies students in the group that DOES have a problem receive a score for their problem.
        """
        self.split_one_group_no_problems_setup(self.user_partition_group_1)

        self.submit_question_answer('H2P1_GROUP1', {'2_1': 'Correct'})

        self.assertEqual(self.score_for_hw('homework1'), [1.0])
        self.assertEqual(self.score_for_hw('homework2'), [1.0])
        self.assertEqual(self.earned_hw_scores(), [1.0, 1.0])

        # Grade percent is .6. Here is the calculation.
        homework_1_score = (1.0 / 2) * 0.8
        homework_2_score = (1.0 / 1) * 0.2
        self.check_grade_percent(round((homework_1_score + homework_2_score), 2))