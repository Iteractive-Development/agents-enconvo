# Detail reference

## Mocking Patterns

### Pattern 1: Mocking Modules

```typescript
// services/email.service.ts
import nodemailer from "nodemailer";

export class EmailService {
  private transporter = nodemailer.createTransport({
    host: process.env.SMTP_HOST,
    port: 587,
    auth: {
      user: process.env.SMTP_USER,
      pass: process.env.SMTP_PASS,
    },
  });

  async sendEmail(to: string, subject: string, html: string) {
    await this.transporter.sendMail({
      from: process.env.EMAIL_FROM,
      to,
      subject,
      html,
    });
  }
}

// services/email.service.test.ts
import { describe, it, expect, vi, beforeEach } from "vitest";
import { EmailService } from "./email.service";

vi.mock("nodemailer", () => ({
  default: {
    createTransport: vi.fn(() => ({
      sendMail: vi.fn().mockResolvedValue({ messageId: "123" }),
    })),
  },
}));

describe("EmailService", () => {
  let service: EmailService;

  beforeEach(() => {
    service = new EmailService();
  });

  it("should send email successfully", async () => {
    await service.sendEmail(
      "test@example.com",
      "Test Subject",
      "<p>Test Body</p>",
    );

    expect(service["transporter"].sendMail).toHaveBeenCalledWith(
      expect.objectContaining({
        to: "test@example.com",
        subject: "Test Subject",
      }),
    );
  });
});
```

### Pattern 2: Dependency Injection for Testing

```typescript
// services/user.service.ts
export interface IUserRepository {
  findById(id: string): Promise<User | null>;
  create(user: User): Promise<User>;
}

export class UserService {
  constructor(private userRepository: IUserRepository) {}

  async getUser(id: string): Promise<User> {
    const user = await this.userRepository.findById(id);
    if (!user) {
      throw new Error("User not found");
    }
    return user;
  }

  async createUser(userData: CreateUserDTO): Promise<User> {
    // Business logic here
    const user = { id: generateId(), ...userData };
    return this.userRepository.create(user);
  }
}

// services/user.service.test.ts
import { describe, it, expect, vi, beforeEach } from "vitest";
import { UserService, IUserRepository } from "./user.service";

describe("UserService", () => {
  let service: UserService;
  let mockRepository: IUserRepository;

  beforeEach(() => {
    mockRepository = {
      findById: vi.fn(),
      create: vi.fn(),
    };
    service = new UserService(mockRepository);
  });

  describe("getUser", () => {
    it("should return user if found", async () => {
      const mockUser = { id: "1", name: "John", email: "john@example.com" };
      vi.mocked(mockRepository.findById).mockResolvedValue(mockUser);

      const user = await service.getUser("1");

      expect(user).toEqual(mockUser);
      expect(mockRepository.findById).toHaveBeenCalledWith("1");
    });

    it("should throw error if user not found", async () => {
      vi.mocked(mockRepository.findById).mockResolvedValue(null);

      await expect(service.getUser("999")).rejects.toThrow("User not found");
    });
  });

  describe("createUser", () => {
    it("should create user successfully", async () => {
      const userData = { name: "John", email: "john@example.com" };
      const createdUser = { id: "1", ...userData };

      vi.mocked(mockRepository.create).mockResolvedValue(createdUser);

      const user = await service.createUser(userData);

      expect(user).toEqual(createdUser);
      expect(mockRepository.create).toHaveBeenCalled();
    });
  });
});
```

### Pattern 3: Spying on Functions

```typescript
// utils/logger.ts
export const logger = {
  info: (message: string) => console.log(`INFO: ${message}`),
  error: (message: string) => console.error(`ERROR: ${message}`),
};

// services/order.service.ts
import { logger } from "../utils/logger";

export class OrderService {
  async processOrder(orderId: string): Promise<void> {
    logger.info(`Processing order ${orderId}`);
    // Process order logic
    logger.info(`Order ${orderId} processed successfully`);
  }
}

// services/order.service.test.ts
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { OrderService } from "./order.service";
import { logger } from "../utils/logger";

describe("OrderService", () => {
  let service: OrderService;
  let loggerSpy: any;

  beforeEach(() => {
    service = new OrderService();
    loggerSpy = vi.spyOn(logger, "info");
  });

  afterEach(() => {
    loggerSpy.mockRestore();
  });

  it("should log order processing", async () => {
    await service.processOrder("123");

    expect(loggerSpy).toHaveBeenCalledWith("Processing order 123");
    expect(loggerSpy).toHaveBeenCalledWith("Order 123 processed successfully");
    expect(loggerSpy).toHaveBeenCalledTimes(2);
  });
});
```

## Integration Testing

Integration tests verify real database operations and HTTP endpoints using `supertest` and a test database instance. Always truncate tables in `beforeEach` and tear down in `afterAll`.

For full API integration test examples (supertest + PostgreSQL) and database repository integration tests, see [./advanced-testing-patterns.md](./advanced-testing-patterns.md).

## Frontend Testing with Testing Library

Test React components by rendering them and querying by role, placeholder, or test ID. Test hooks with `renderHook` + `act`. Prefer semantic queries (`getByRole`, `getByPlaceholderText`) over `data-testid`.

For complete React component test examples (UserForm, hooks with `renderHook`/`act`), see [./advanced-testing-patterns.md](./advanced-testing-patterns.md).

## Test Fixtures and Factories

Use `@faker-js/faker` to generate realistic test data factories. Factories accept optional `overrides` so tests can set only the fields they care about:

```typescript
// tests/fixtures/user.fixture.ts
import { faker } from "@faker-js/faker";

export function createUserFixture(overrides?: Partial<User>): User {
  return {
    id: faker.string.uuid(),
    name: faker.person.fullName(),
    email: faker.internet.email(),
    createdAt: faker.date.past(),
    ...overrides,
  };
}
```

For snapshot testing, coverage configuration, test organization patterns, promise testing, and timer mocking, see [./advanced-testing-patterns.md](./advanced-testing-patterns.md).

## Best Practices

1. **Follow AAA Pattern**: Arrange, Act, Assert
2. **One assertion per test**: Or logically related assertions
3. **Descriptive test names**: Should describe what is being tested
4. **Use beforeEach/afterEach**: For setup and teardown
5. **Mock external dependencies**: Keep tests isolated
6. **Test edge cases**: Not just happy paths
7. **Avoid implementation details**: Test behavior, not implementation
8. **Use test factories**: For consistent test data
9. **Keep tests fast**: Mock slow operations
10. **Write tests first (TDD)**: When possible
11. **Maintain test coverage**: Aim for 80%+ coverage
12. **Use TypeScript**: For type-safe tests
13. **Test error handling**: Not just success cases
14. **Use data-testid sparingly**: Prefer semantic queries
15. **Clean up after tests**: Prevent test pollution
