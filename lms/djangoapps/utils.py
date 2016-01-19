from django.conf import settings
from django.core.urlresolvers import reverse
from opaque_keys.edx.keys import CourseKey
from xmodule.modulestore.django import modulestore


def get_url_course_enroll():
    course_key = CourseKey.from_string(settings.COURSE_KEY_ENROLL)
    course = modulestore().get_course(course_key)
    urlargs = {'course_id': settings.COURSE_KEY_ENROLL}

    try:
        chapter = course.get_children()[0]
        section = chapter.get_children()[0]
    except IndexError:
        pass
    else:
        urlargs.update({
            'chapter': chapter.url_name,
            'section': section.url_name
        })
        return reverse('courseware_section', kwargs=urlargs)
