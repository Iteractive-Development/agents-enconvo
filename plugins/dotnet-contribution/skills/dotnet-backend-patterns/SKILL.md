---
name: dotnet-backend-patterns
description: Master C#/.NET backend development patterns for building robust APIs, MCP servers, and enterprise applications. Covers async/await, dependency injection, Entity Framework Core, Dapper, configuration, caching, and testing with xUnit. Use when developing .NET backends, reviewing C# code, or designing API architectures.
---

# .NET Backend Development Patterns

Master C#/.NET patterns for building production-grade APIs, MCP servers, and enterprise backends with modern best practices (2024/2025).

## When to Use This Skill

- Developing new .NET Web APIs or MCP servers
- Reviewing C# code for quality and performance
- Designing service architectures with dependency injection
- Implementing caching strategies with Redis
- Writing unit and integration tests
- Optimizing database access with EF Core or Dapper
- Configuring applications with IOptions pattern
- Handling errors and implementing resilience patterns

## Core Concepts

### 1. Project Structure (Clean Architecture)

```
src/
├── Domain/                     # Core business logic (no dependencies)
│   ├── Entities/
│   ├── Interfaces/
│   ├── Exceptions/
│   └── ValueObjects/
├── Application/                # Use cases, DTOs, validation
│   ├── Services/
│   ├── DTOs/
│   ├── Validators/
│   └── Interfaces/
├── Infrastructure/             # External implementations
│   ├── Data/                   # EF Core, Dapper repositories
│   ├── Caching/                # Redis, Memory cache
│   ├── External/               # HTTP clients, third-party APIs
│   └── DependencyInjection/    # Service registration
└── Api/                        # Entry point
    ├── Controllers/            # Or MinimalAPI endpoints
    ├── Middleware/
    ├── Filters/
    └── Program.cs
```

### 2. Dependency Injection Patterns

```csharp
// Service registration by lifetime
public static class ServiceCollectionExtensions
{
    public static IServiceCollection AddApplicationServices(
        this IServiceCollection services,
        IConfiguration configuration)
    {
        // Scoped: One instance per HTTP request
        services.AddScoped<IProductService, ProductService>();
        services.AddScoped<IOrderService, OrderService>();

        // Singleton: One instance for app lifetime
        services.AddSingleton<ICacheService, RedisCacheService>();
        services.AddSingleton<IConnectionMultiplexer>(_ =>
            ConnectionMultiplexer.Connect(configuration["Redis:Connection"]!));

        // Transient: New instance every time
        services.AddTransient<IValidator<CreateOrderRequest>, CreateOrderValidator>();

        // Options pattern for configuration
        services.Configure<CatalogOptions>(configuration.GetSection("Catalog"));
        services.Configure<RedisOptions>(configuration.GetSection("Redis"));

        // Factory pattern for conditional creation
        services.AddScoped<IPriceCalculator>(sp =>
        {
            var options = sp.GetRequiredService<IOptions<PricingOptions>>().Value;
            return options.UseNewEngine
                ? sp.GetRequiredService<NewPriceCalculator>()
                : sp.GetRequiredService<LegacyPriceCalculator>();
        });

        // Keyed services (.NET 8+)
        services.AddKeyedScoped<IPaymentProcessor, StripeProcessor>("stripe");
        services.AddKeyedScoped<IPaymentProcessor, PayPalProcessor>("paypal");

        return services;
    }
}

// Usage with keyed services
public class CheckoutService
{
    public CheckoutService(
        [FromKeyedServices("stripe")] IPaymentProcessor stripeProcessor)
    {
        _processor = stripeProcessor;
    }
}
```

### 3. Async/Await Patterns

```csharp
// ✅ CORRECT: Async all the way down
public async Task<Product> GetProductAsync(string id, CancellationToken ct = default)
{
    return await _repository.GetByIdAsync(id, ct);
}

// ✅ CORRECT: Parallel execution with WhenAll
public async Task<(Stock, Price)> GetStockAndPriceAsync(
    string productId,
    CancellationToken ct = default)
{
    var stockTask = _stockService.GetAsync(productId, ct);
    var priceTask = _priceService.GetAsync(productId, ct);

    await Task.WhenAll(stockTask, priceTask);

    return (await stockTask, await priceTask);
}

// ✅ CORRECT: ConfigureAwait in libraries
public async Task<T> LibraryMethodAsync<T>(CancellationToken ct = default)
{
    var result = await _httpClient.GetAsync(url, ct).ConfigureAwait(false);
    return await result.Content.ReadFromJsonAsync<T>(ct).ConfigureAwait(false);
}

// ✅ CORRECT: ValueTask for hot paths with caching
public ValueTask<Product?> GetCachedProductAsync(string id)
{
    if (_cache.TryGetValue(id, out Product? product))
        return ValueTask.FromResult(product);

    return new ValueTask<Product?>(GetFromDatabaseAsync(id));
}

// ❌ WRONG: Blocking on async (deadlock risk)
var result = GetProductAsync(id).Result;  // NEVER do this
var result2 = GetProductAsync(id).GetAwaiter().GetResult(); // Also bad

// ❌ WRONG: async void (except event handlers)
public async void ProcessOrder() { }  // Exceptions are lost

// ❌ WRONG: Unnecessary Task.Run for already async code
await Task.Run(async () => await GetDataAsync());  // Wastes thread
```

### 4. Configuration with IOptions

```csharp
// Configuration classes
public class CatalogOptions
{
    public const string SectionName = "Catalog";

    public int DefaultPageSize { get; set; } = 50;
    public int MaxPageSize { get; set; } = 200;
    public TimeSpan CacheDuration { get; set; } = TimeSpan.FromMinutes(15);
    public bool EnableEnrichment { get; set; } = true;
}

public class RedisOptions
{
    public const string SectionName = "Redis";

    public string Connection { get; set; } = "localhost:6379";
    public string KeyPrefix { get; set; } = "mcp:";
    public int Database { get; set; } = 0;
}

// appsettings.json
{
    "Catalog": {
        "DefaultPageSize": 50,
        "MaxPageSize": 200,
        "CacheDuration": "00:15:00",
        "EnableEnrichment": true
    },
    "Redis": {
        "Connection": "localhost:6379",
        "KeyPrefix": "mcp:",
        "Database": 0
    }
}

// Registration
services.Configure<CatalogOptions>(configuration.GetSection(CatalogOptions.SectionName));
services.Configure<RedisOptions>(configuration.GetSection(RedisOptions.SectionName));

// Usage with IOptions (singleton, read once at startup)
public class CatalogService
{
    private readonly CatalogOptions _options;

    public CatalogService(IOptions<CatalogOptions> options)
    {
        _options = options.Value;
    }
}

// Usage with IOptionsSnapshot (scoped, re-reads on each request)
public class DynamicService
{
    private readonly CatalogOptions _options;

    public DynamicService(IOptionsSnapshot<CatalogOptions> options)
    {
        _options = options.Value;  // Fresh value per request
    }
}

// Usage with IOptionsMonitor (singleton, notified on changes)
public class MonitoredService
{
    private CatalogOptions _options;

    public MonitoredService(IOptionsMonitor<CatalogOptions> monitor)
    {
        _options = monitor.CurrentValue;
        monitor.OnChange(newOptions => _options = newOptions);
    }
}
```

### 5. Result Pattern (Avoiding Exceptions for Flow Control)

```csharp
// Generic Result type
public class Result<T>
{
    public bool IsSuccess { get; }
    public T? Value { get; }
    public string? Error { get; }
    public string? ErrorCode { get; }

    private Result(bool isSuccess, T? value, string? error, string? errorCode)
    {
        IsSuccess = isSuccess;
        Value = value;
        Error = error;
        ErrorCode = errorCode;
    }

    public static Result<T> Success(T value) => new(true, value, null, null);
    public static Result<T> Failure(string error, string? code = null) => new(false, default, error, code);

    public Result<TNew> Map<TNew>(Func<T, TNew> mapper) =>
        IsSuccess ? Result<TNew>.Success(mapper(Value!)) : Result<TNew>.Failure(Error!, ErrorCode);

    public async Task<Result<TNew>> MapAsync<TNew>(Func<T, Task<TNew>> mapper) =>
        IsSuccess ? Result<TNew>.Success(await mapper(Value!)) : Result<TNew>.Failure(Error!, ErrorCode);
}

// Usage in service
public async Task<Result<Order>> CreateOrderAsync(CreateOrderRequest request, CancellationToken ct)
{
    // Validation
    var validation = await _validator.ValidateAsync(request, ct);
    if (!validation.IsValid)
        return Result<Order>.Failure(
            validation.Errors.First().ErrorMessage,
            "VALIDATION_ERROR");

    // Business rule check
    var stock = await _stockService.CheckAsync(request.ProductId, request.Quantity, ct);
    if (!stock.IsAvailable)
        return Result<Order>.Failure(
            $"Insufficient stock: {stock.Available} available, {request.Quantity} requested",
            "INSUFFICIENT_STOCK");

    // Create order
    var order = await _repository.CreateAsync(request.ToEntity(), ct);

    return Result<Order>.Success(order);
}

// Usage in controller/endpoint
app.MapPost("/orders", async (
    CreateOrderRequest request,
    IOrderService orderService,
    CancellationToken ct) =>
{
    var result = await orderService.CreateOrderAsync(request, ct);

    return result.IsSuccess
        ? Results.Created($"/orders/{result.Value!.Id}", result.Value)
        : Results.BadRequest(new { error = result.Error, code = result.ErrorCode });
});
```


## Detailed reference

Code examples, extended tables, and walk-throughs live in [references/details.md](references/details.md)
so the skill stays light in context.
