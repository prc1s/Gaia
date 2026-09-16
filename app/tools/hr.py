from app.errors import PermanentToolError

EMPLOYEES = {
    "emp-1001": {
        "first_name": "Sara",
        "last_name": "Almutairi",
        "personal_email": "sara.almutairi@gmail.com",
        "role": "Backend Engineer",
        "department": "Engineering",
        "manager_id": "emp-2001",
        "manager_email": "layla.hassan@gaia.sa",
        "start_date": "2026-10-01",
    },
    "emp-1002": {
        "first_name": "Omar",
        "last_name": "Al-Faraj",
        "personal_email": "omar.faraj@gmail.com",
        "role": "Data Scientist",
        "department": "Engineering",
        "manager_id": "emp-2001",
        "manager_email": "layla.hassan@gaia.sa",
        "start_date": "2026-10-15",
    },
    "emp-1003": {
        "first_name": "Néstor",
        "last_name": "González",
        "personal_email": "nestor.gonzalez@gmail.com",
        "role": "Ceramics Technician",
        "department": "Operations",
        "manager_id": "emp-2002",
        "manager_email": "faisal.otaibi@gaia.sa",
        "start_date": "2026-11-01",
    },
}


async def get_employee(db, employee_id: str) -> dict:
    """Read-only. Unknown employee is permanent: a human should look."""
    employee = EMPLOYEES.get(employee_id)
    if employee is None:
        raise PermanentToolError(f"employee not found: {employee_id}")
    return dict(employee)
