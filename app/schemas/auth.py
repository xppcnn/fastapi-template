from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)
    organization_name: str | None = Field(default=None, min_length=1, max_length=200)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class UserResponse(BaseModel):
    public_id: UUID
    email: EmailStr
    is_active: bool


class OrganizationResponse(BaseModel):
    public_id: UUID
    name: str
    status: str
    role: str


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse
    organization: OrganizationResponse


class MeResponse(BaseModel):
    user: UserResponse
    organization: OrganizationResponse
