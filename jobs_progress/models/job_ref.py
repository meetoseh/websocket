from pydantic import BaseModel, Field


class JobRef(BaseModel):
    """A reference to progress information on a job; this could perhaps
    be more accurately thought of as a job progress ref, but there is no
    job uid for the most part so it's convenient to just think of this as
    a job ref.
    """

    uid: str = Field(
        description="The unique identifier for the job progress that can be tracked"
    )
    jwt: str = Field(
        description="The JWT that can be used to fetch job progress information"
    )
