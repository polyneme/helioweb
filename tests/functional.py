"""
Functional tests
"""

class Person:
    def __init__(self, id_):
        self.id = id_
        self.name = None

    def __repr__(self):
        return f"{self.id=}, {self.name=}"


class Attendee:
    def __init__(self, person, conference):
        self.person = person
        self.conference = conference

    def name(self):
        return not self.person.name

    def __repr__(self):
        return f"{self.person.name} at {self.conference.name}"

    def attach_profile(self, profile):
        raise NotImplementedError


class Conference:
    def __init__(self, name):
        self.name = name
        self.attendee_pool = []

    def add_to_attendee_pool(self, attendee):
        self.attendee_pool.append(attendee)

    def get_attendee_pool(self):
        return self.attendee_pool

    def __repr__(self):
        return f"{self.name=}, {self.attendee_pool=}"


def test_profile_discoverable_in_attendee_pool():
    conference = Conference()
    profile = make_one_attendee_profile_for(conference)
    attendees = make_attendees(conference)
    attendee = get_one(attendees)
    attendee.attach_profile(profile)
    conference.add_to_attendee_pool(attendee)
    assert profile in [attendee.profile for attendee in conference.get_attendee_pool()]

