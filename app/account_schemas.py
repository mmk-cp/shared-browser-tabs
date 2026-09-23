from pydantic import BaseModel, ConfigDict, Field, field_validator


def valid_password(value: str) -> str:
    if len(value.encode('utf-8')) > 72:
        raise ValueError('Password must be at most 72 UTF-8 bytes')
    return value


class NewCredentials(BaseModel):
    model_config = ConfigDict(extra='forbid')
    username: str = Field(min_length=2, max_length=120)
    password: str = Field(min_length=8, max_length=72)

    @field_validator('username', mode='before')
    @classmethod
    def trim_username(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator('password')
    @classmethod
    def check_password(cls, value):
        return valid_password(value)


class PasswordChange(BaseModel):
    model_config = ConfigDict(extra='forbid')
    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=8, max_length=72)

    @field_validator('new_password')
    @classmethod
    def check_password(cls, value):
        return valid_password(value)
